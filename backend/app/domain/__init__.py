"""Domain layer: entities, enums, state transitions, validation, and business rules.

The domain enums (media types, download status) are the shared persistence
contract: ORM models and API schemas import them from here so later tasks
extend this layer instead of duplicating vocabulary.
"""

from app.domain.enums import DownloadStatus, MediaType

__all__ = ["DownloadStatus", "MediaType"]
