"""Domain layer: entities, enums, state transitions, validation, and business rules.

The domain package is the stable contract for the rest of the application:
application services (Tasks 7-10) and the worker (Tasks 11-12) import these
objects instead of redefining vocabulary. It exports:

* **Enums** — :class:`MediaType`, :class:`DownloadStatus` (the shared
  persistence/API vocabulary).
* **Value objects** — parse/download commands and results
  (:mod:`app.domain.models`).
* **Path helpers** — slugified safe filenames and root-contained path
  building (:mod:`app.domain.paths`).
* **State transitions** — the legal ``DownloadStatus`` graph
  (:mod:`app.domain.transitions`).
"""

from app.domain.enums import DownloadStatus, MediaType
from app.domain.models import (
    DownloadCommand,
    DownloadProgress,
    DownloadResult,
    ParseCommand,
    ParseResult,
)
from app.domain.paths import (
    PathOutsideRootError,
    build_path,
    is_within,
    safe_media_filename,
    slugify,
)
from app.domain.transitions import (
    ALLOWED_TRANSITIONS,
    IllegalTransitionError,
    transition,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "DownloadCommand",
    "DownloadProgress",
    "DownloadResult",
    "DownloadStatus",
    "IllegalTransitionError",
    "MediaType",
    "ParseCommand",
    "ParseResult",
    "PathOutsideRootError",
    "build_path",
    "is_within",
    "safe_media_filename",
    "slugify",
    "transition",
]
