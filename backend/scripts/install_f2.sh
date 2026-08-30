#!/usr/bin/env bash
# Backwards-compatible entry point for native deployments.
#
# The old implementation installed the upstream f2 wheel with --no-deps,
# leaving its conflicting metadata behind. Delegate to the cross-platform
# installer, which builds and installs both verified compatibility wheels and
# finishes with `python -m pip check`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  PY_BIN="$VIRTUAL_ENV/bin/python"
elif [ -x "$SCRIPT_DIR/../../.venv/bin/python" ]; then
  PY_BIN="$SCRIPT_DIR/../../.venv/bin/python"
elif [ -x "$SCRIPT_DIR/../../.venv/Scripts/python.exe" ]; then
  PY_BIN="$SCRIPT_DIR/../../.venv/Scripts/python.exe"
else
  PY_BIN="$(command -v python || true)"
  if [ -z "$PY_BIN" ]; then
    echo "ERROR: no Python interpreter found — activate the venv first" >&2
    exit 1
  fi
fi

exec "$PY_BIN" "$SCRIPT_DIR/install_backend_dependencies.py" "$@"
