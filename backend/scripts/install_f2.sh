#!/usr/bin/env bash
# Install the f2 video parser without its dependency metadata.
#
# f2 0.0.1.7 hard-pins pytest==8.3.4, cryptography==44.0.0, m3u8==3.6.0,
# protobuf==5.28.3, websockets<13.0, and websockets-proxy==0.1.2, which
# conflict with this repo's pins — see the f2 block in backend/requirements.txt
# for the explicit runtime deps, so --no-deps is safe: f2's code only uses
# stable APIs and runs on the repo's newer shared-library versions.
#
# MUST run AFTER `pip install -r backend/requirements.txt`: the import check
# below needs the declared deps (rich/m3u8/click/protobuf/...).
#
# The interpreter is resolved from $VIRTUAL_ENV (deploy-native.sh passes it)
# with the repo's own venv / PATH `python` as fallbacks, so this script works
# both standalone and when invoked from deploy-native.sh.
set -euo pipefail

if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  PY_BIN="$VIRTUAL_ENV/bin/python"
elif [ -x "$(dirname "$0")/../../.venv/bin/python" ]; then
  PY_BIN="$(cd "$(dirname "$0")/../.." && pwd)/.venv/bin/python"
else
  PY_BIN="$(command -v python || true)"
  if [ -z "$PY_BIN" ]; then
    echo "ERROR: no python interpreter found — activate the venv first" >&2
    exit 1
  fi
fi

"$PY_BIN" -m pip install --no-deps "f2==0.0.1.7"

# Network-free import sanity check (utils/exceptions cover the cryptography
# compatibility risk; do NOT import the douyin/tiktok handler modules — they
# fetch external token APIs at import time).
"$PY_BIN" -c "import f2.exceptions, f2.utils.utils"
