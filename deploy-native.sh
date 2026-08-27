#!/usr/bin/env bash
# Koi Fetch — native (non-Docker) one-command deploy in REAL-ENGINE mode.
#
# Builds and runs the full stack on the host, in engine mode
# (PARSER_ENGINE/DOWNLOADER_ENGINE=engine → f2 / parse-video-py + musicdl):
#
#   1. venv + backend deps        (pip; writable cache inside .venv)
#   2. musicdl Python-3.10 shim   (backend/scripts/patch_musicdl_py310.py)
#   3. f2 parser (no-deps)        (backend/scripts/install_f2.sh)
#   4. frontend build             (npm ci/install + vite build → frontend/dist)
#   5. migrations + admin seed    (alembic upgrade head, app.infrastructure.seed)
#   6. API + worker daemons       (background, PID files, logs)
#   7. health wait + URL
#
# The repo-root data/ directory may be root-owned (a legacy deployment created
# it), so runtime data defaults to backend/data/ (writable). Override with
# KOI_DATA_ROOT. The default port is 8010 — a legacy deployment often owns
# 8000; override with KOI_PORT.
#
# Usage:
#   ./deploy-native.sh            # start (default)
#   ./deploy-native.sh stop       # stop API + worker (by PID files)
#   ./deploy-native.sh restart    # stop + start
#   ./deploy-native.sh status     # PIDs + health
#
# Env (all optional):
#   KOI_PORT=8010  KOI_HOST=127.0.0.1  KOI_DATA_ROOT=backend/data
#   PARSER_ENGINE=engine  DOWNLOADER_ENGINE=engine
#   PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple   (fast mirror for CN)
#   SKIP_DEPS=1      skip pip/npm install + frontend build (restart only)
#   SKIP_FRONTEND=1  skip the frontend build
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

KOI_PORT="${KOI_PORT:-8010}"
KOI_HOST="${KOI_HOST:-127.0.0.1}"
KOI_DATA_ROOT="${KOI_DATA_ROOT:-backend/data}"
PARSER_ENGINE="${PARSER_ENGINE:-engine}"
DOWNLOADER_ENGINE="${DOWNLOADER_ENGINE:-engine}"
LOG_DIR="$ROOT/.deploy-logs"
API_PID="$LOG_DIR/api.pid"
WORKER_PID="$LOG_DIR/worker.pid"
PY="$ROOT/.venv/bin/python"
NPM_CACHE="${NPM_CACHE:-/tmp/npm-cache}"
PIP_CACHE="$ROOT/.venv/pip-cache"

# Absolute data root (relative → against repo root) so sqlite/storage paths
# never depend on the caller's CWD at runtime.
case "$KOI_DATA_ROOT" in
  /*) DATA_ABS="$KOI_DATA_ROOT" ;;
  *)  DATA_ABS="$ROOT/$KOI_DATA_ROOT" ;;
esac
DATABASE_URL="sqlite:///$DATA_ABS/db/koifetch.db"

export PARSER_ENGINE DOWNLOADER_ENGINE DATABASE_URL
export VIDEO_STORAGE_PATH="$DATA_ABS/pond/video"
export IMAGE_STORAGE_PATH="$DATA_ABS/pond/image"
export MUSIC_STORAGE_PATH="$DATA_ABS/pond/music"
export TEMP_VIDEO_PATH="$DATA_ABS/bubble/video"
export TEMP_IMAGE_PATH="$DATA_ABS/bubble/image"
export TEMP_MUSIC_PATH="$DATA_ABS/bubble/music"

log() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

start() {
  # --- 1-3. backend deps + musicdl shim + f2 parser ---------------------
  if [ "${SKIP_DEPS:-0}" != "1" ]; then
    if [ ! -x "$PY" ]; then
      log "creating venv"
      python3 -m venv "$ROOT/.venv"
    fi
    log "installing backend dependencies (cache: $PIP_CACHE)"
    "$PY" -m pip install --upgrade pip -q --cache-dir "$PIP_CACHE" 2>/dev/null || true
    "$PY" -m pip install -r "$ROOT/backend/requirements.txt" \
      --cache-dir "$PIP_CACHE" ${PIP_INDEX:+-i "$PIP_INDEX"}
    log "patching musicdl for Python 3.10 (typing.Unpack shim)"
    "$PY" "$ROOT/backend/scripts/patch_musicdl_py310.py"
    log "installing f2 parser (no-deps + import check)"
    VIRTUAL_ENV="$ROOT/.venv" "$ROOT/backend/scripts/install_f2.sh"
  fi

  # --- 4. frontend build -------------------------------------------------
  if [ "${SKIP_DEPS:-0}" != "1" ] && [ "${SKIP_FRONTEND:-0}" != "1" ]; then
    if [ ! -d "$ROOT/frontend/node_modules" ]; then
      log "installing frontend dependencies"
      npm install --prefix frontend --cache "$NPM_CACHE"
    fi
    log "building frontend"
    npm run build --prefix frontend
  fi

  # --- 5. migrations + admin seed ---------------------------------------
  log "migrations + admin seed"
  ( cd "$ROOT/backend" && "$ROOT/.venv/bin/python" -m alembic upgrade head )
  ( cd "$ROOT/backend" && "$ROOT/.venv/bin/python" -m app.infrastructure.seed )

  # --- 6. daemons --------------------------------------------------------
  stop || true
  mkdir -p "$LOG_DIR" "$DATA_ABS/db" "$DATA_ABS/pond" "$DATA_ABS/bubble"
  log "starting API on http://$KOI_HOST:$KOI_PORT  (logs: $LOG_DIR/api.log)"
  cd "$ROOT"
  PYTHONPATH="$ROOT/backend" nohup "$PY" -m uvicorn app.main:app \
    --host "$KOI_HOST" --port "$KOI_PORT" >"$LOG_DIR/api.log" 2>&1 &
  echo $! > "$API_PID"
  PYTHONPATH="$ROOT/backend" nohup "$PY" -m app.workers.main >"$LOG_DIR/worker.log" 2>&1 &
  echo $! > "$WORKER_PID"

  # --- 7. health wait ----------------------------------------------------
  log "waiting for /api/health ..."
  for i in $(seq 1 60); do
    if curl -fsS "http://$KOI_HOST:$KOI_PORT/api/health" >/dev/null 2>&1; then
      log "healthy after ${i}s"
      curl -s "http://$KOI_HOST:$KOI_PORT/api/health"
      echo
      log "Koi Fetch is live: http://$KOI_HOST:$KOI_PORT/  (worker pid $(cat "$WORKER_PID"))"
      log "stop with: $0 stop"
      return 0
    fi
    if ! kill -0 "$(cat "$API_PID")" 2>/dev/null; then
      die "API process exited early — see $LOG_DIR/api.log"
    fi
    sleep 1
  done
  die "API did not become healthy within 60s — see $LOG_DIR/api.log"
}

stop() {
  for pidfile in "$API_PID" "$WORKER_PID"; do
    if [ -f "$pidfile" ]; then
      pid="$(cat "$pidfile")"
      if kill -0 "$pid" 2>/dev/null; then
        log "stopping pid $pid ($(basename "$pidfile"))"
        kill "$pid" 2>/dev/null || true
        for _ in $(seq 1 10); do
          kill -0 "$pid" 2>/dev/null || break
          sleep 0.3
        done
        kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
      fi
      rm -f "$pidfile"
    fi
  done
}

status() {
  echo "API pid:    $(cat "$API_PID" 2>/dev/null || echo '—')"
  echo "Worker pid: $(cat "$WORKER_PID" 2>/dev/null || echo '—')"
  curl -fsS "http://$KOI_HOST:$KOI_PORT/api/health" 2>/dev/null \
    && echo || echo "API not responding on http://$KOI_HOST:$KOI_PORT"
}

case "${1:-start}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  *) die "unknown command: $1 (start|stop|restart|status)" ;;
esac
