#!/usr/bin/env bash
# Koi Fetch — one-command local deploy.
#
# Rebuilds and restarts the local docker compose stack. The backend image
# compiles the frontend inside it, so `--build` (not a plain restart) is
# required to pick up frontend changes. After the stack is up, waits until
# the backend reports healthy, then prints the URL.
#
# Usage:  ./deploy.sh
# Needs:  docker compose, curl (health wait)
set -euo pipefail

# Docker CLI needs a writable config dir (~/.docker). If it cannot be created
# or written (e.g. a root-owned or sandboxed home), point DOCKER_CONFIG at a
# temp dir instead of failing on `mkdir /home/.../.docker: permission denied`.
if ! mkdir -p "${HOME}/.docker" 2>/dev/null || [ ! -w "${HOME}/.docker" ]; then
  export DOCKER_CONFIG="$(mktemp -d /tmp/koi-docker-config.XXXXXX)"
  echo "==> ~/.docker not usable — using DOCKER_CONFIG=${DOCKER_CONFIG}"
fi

# Always run from the repo root (the compose file and .env live there).
cd "$(dirname "$0")"

echo "==> docker compose up --build -d"
docker compose up --build -d

echo "==> Waiting for http://localhost:8000/api/health ..."
for i in $(seq 1 90); do
  if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then
    echo "    healthy after ${i}s"
    curl -s http://localhost:8000/api/health
    echo
    echo "==> Koi Fetch is live: http://localhost:8000/"
    exit 0
  fi
  sleep 1
done

echo "backend did not become healthy within 90s — inspect: docker compose logs backend" >&2
exit 1
