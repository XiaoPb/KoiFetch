#!/usr/bin/env bash
# Install the f2 video parser without its dependency metadata.
#
# f2 0.0.1.7 hard-pins pytest==8.3.4 and cryptography==44.0.0, which conflict
# with this repo's pytest>=8.4 and musicdl's cryptography>=46.0.5,<47. Its
# runtime dependencies are declared explicitly in backend/requirements.txt
# (the f2 block), so --no-deps is safe: f2's code only uses stable
# cryptography APIs and runs on the repo's newer shared-library versions.
set -euo pipefail
pip install --no-deps "f2==0.0.1.7"
