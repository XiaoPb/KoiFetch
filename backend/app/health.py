"""Container readiness probe for the backend healthcheck.

The Compose backend healthcheck runs ``python -m app.health`` (in the backend
container) instead of a naive HTTP 200 check: ``/api/health`` always returns
200 and reports readiness in the body (``code``), so a degraded service (e.g.
an uncreatable storage root) would otherwise pass a plain liveness probe. This
module requires ``code == 0`` and exits non-zero otherwise, so
``depends_on: service_healthy`` reflects service AND storage readiness.
"""

from __future__ import annotations

import json
import os
from urllib.request import urlopen

__all__ = ["HEALTH_URL", "is_ready", "probe", "main"]

HEALTH_URL = "http://127.0.0.1:8000/api/health"


def is_ready(body: object) -> bool:
    """True when a health response body reports full readiness (``code == 0``)."""
    return isinstance(body, dict) and body.get("code") == 0


def probe(url: str = "") -> bool:
    """Return True when the health endpoint reports full readiness.

    Connection failures, non-200 responses, and bodies with ``code != 0`` all
    count as not ready. ``url`` defaults to :data:`HEALTH_URL` and can be
    overridden via the ``HEALTHCHECK_URL`` environment variable.
    """
    default_url = os.environ.get("HEALTHCHECK_URL", HEALTH_URL)
    try:
        with urlopen(url or default_url, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception:
        return False
    return is_ready(body)


def main() -> int:
    """CLI entrypoint: exit 0 when ready, 1 otherwise."""
    return 0 if probe() else 1


if __name__ == "__main__":
    raise SystemExit(main())
