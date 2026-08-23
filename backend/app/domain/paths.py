"""Safe filename and path helpers for the domain layer.

Everything that turns untrusted input (platform titles, author names, source
ids, extensions) into filesystem names or paths lives here. The PRD requires
that path segments be *slugified* — restricted to ASCII letters, digits,
``-`` and ``_`` (slugs emit only ``[a-z0-9-]``), capped at ~120 characters —
so that hostile titles cannot traverse or overwrite outside the configured
storage roots (``../``, absolute paths, null bytes, reserved characters are
all neutralized).

Two layers of defense are provided:

* :func:`slugify` / :func:`safe_media_filename` produce safe *names* — the
  filename pattern per PRD §3.3.6 (``<published_at>_<title_slug>_<source_id>
  .<ext>`` for video/music, numbered ``<index:03d>_<title_slug>.<ext>`` for
  images).
* :func:`is_within` / :func:`build_path` guarantee *containment* — a candidate
  path may not escape a configured root, even through symlinks. This is the
  final gate: callers compose slugs into components, and ``build_path`` rejects
  anything that resolves outside the root (raising
  :class:`PathOutsideRootError`).

Design notes (documented decisions):

* **ASCII-only slugs.** The task/spec charset is ``[a-zA-Z0-9_-]``, so
  non-ASCII letters (including CJK) are dropped. A title that slugs to nothing
  falls back to ``fallback`` (default ``"untitled"``); uniqueness is preserved
  by other filename components such as ``source_id``. If CJK-preserving slugs
  are wanted later, relax the regex to ``[^\\w-]`` with ``re.UNICODE`` — the
  containment checks are independent of the charset.
* **Truncation with a hash suffix.** When the slug exceeds ``max_length`` it is
  truncated and a stable 8-hex sha256 suffix of the *original* (NFKD) text is
  appended, so distinct long titles never collide. When ``max_length`` is too
  small to fit the suffix it is truncated without one (caller-chosen tiny
  limits; documented behavior, keep ``max_length >= 9`` for uniqueness).
* **NFKD normalization** decomposes accented Latin (``café`` -> ``cafe``) and
  full-width forms before the ASCII filter.
* **Containment resolves symlinks.** ``is_within`` compares realpaths (via
  ``os.path.realpath``) of the absolute forms of both paths, using
  ``os.path.normcase`` so Windows drive/case differences are handled and
  ``os.path.commonpath`` so prefix look-alikes (``C:\\a`` vs ``C:\\ab``) can
  never pass. Both inputs may be relative; they are resolved against the
  current working directory, so callers should pass absolute roots (the
  configured storage roots are).
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

from app.domain.enums import MediaType

__all__ = [
    "PathOutsideRootError",
    "build_path",
    "is_within",
    "safe_media_filename",
    "slugify",
]

DEFAULT_SLUG_MAX_LENGTH = 120
_HASH_SUFFIX_LENGTH = 8
_NON_SLUG_CHARS = re.compile(r"[^a-zA-Z0-9]+")
_NON_EXT_CHARS = re.compile(r"[^a-zA-Z0-9]+")


class PathOutsideRootError(ValueError):
    """Raised when a candidate path escapes its configured storage root."""


def slugify(
    text: object,
    max_length: int = DEFAULT_SLUG_MAX_LENGTH,
    fallback: str = "untitled",
) -> str:
    """Turn untrusted text into a safe path segment.

    The result contains only ``[a-z0-9-]`` (a subset of the PRD charset
    ``[a-zA-Z0-9_-]``): text is NFKD-normalized, every run of non-alphanumeric
    characters — spaces, punctuation, ``-``, ``_``, slashes — collapses to a
    single ``-``, the result is stripped of leading/trailing separators and
    lowercased. A slug that is empty after filtering returns ``fallback``
    verbatim.

    Slugs longer than ``max_length`` are truncated and given a stable 8-hex
    sha256 suffix of the source text, keeping the total within ``max_length``
    (see module docstring for the tiny-limit edge case).
    """
    if max_length < 1:
        raise ValueError("max_length must be >= 1")
    if not isinstance(text, str):
        text = str(text)
    normalized = unicodedata.normalize("NFKD", text)
    slug = _NON_SLUG_CHARS.sub("-", normalized).strip("-").lower()
    if not slug:
        return fallback
    if len(slug) <= max_length:
        return slug
    if max_length < _HASH_SUFFIX_LENGTH + 2:
        return slug[:max_length]
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[
        :_HASH_SUFFIX_LENGTH
    ]
    head = slug[: max_length - _HASH_SUFFIX_LENGTH - 1].rstrip("-")
    return f"{head}-{digest}"


def safe_media_filename(
    media_type: MediaType,
    *,
    published_at: date | datetime | str,
    title: str,
    source_id: str | None = None,
    ext: str,
    index: int | None = None,
) -> str:
    """Build a PRD-style media filename with every component slugified.

    * video/music: ``<published_at>_<title_slug>_<source_id>.<ext>``
    * image: ``<index:03d>_<title_slug>.<ext>``

    ``published_at`` may be a ``date``, ``datetime`` or ISO-8601 string and is
    normalized to ``YYYY-MM-DD``. ``ext`` is sanitized to ``[a-z0-9]+`` (any
    non-alphanumeric characters, including dots and slashes, are removed and
    the result lowercased) so an extension can never smuggle path separators.
    Only the returned name is produced here — no filesystem access; directory
    building under a root is the caller's job via :func:`build_path`.
    """
    ext = _clean_extension(ext)
    if media_type is MediaType.IMAGE:
        if index is None:
            raise ValueError("index is required for image filenames")
        if index < 0:
            raise ValueError("index must be >= 0")
        return f"{index:03d}_{slugify(title)}.{ext}"
    if media_type not in (MediaType.VIDEO, MediaType.MUSIC):
        raise ValueError(f"unsupported media type: {media_type!r}")
    if not source_id:
        raise ValueError("source_id is required for video/music filenames")
    published = _format_published_at(published_at)
    return f"{published}_{slugify(title)}_{slugify(source_id)}.{ext}"


def is_within(root: Path | str, candidate: Path | str) -> bool:
    """Return ``True`` when ``candidate`` resolves inside ``root``.

    Both paths are made absolute and symlinks resolved (``os.path.realpath``)
    before comparison, so ``../`` escapes and symlink escapes are detected.
    ``candidate == root`` counts as within. Returns ``False`` (never raises)
    for disjoint roots such as different Windows drives.
    """
    root_abs = _absolute_real(root)
    candidate_abs = _absolute_real(candidate)
    try:
        common = os.path.commonpath(
            [os.path.normcase(root_abs), os.path.normcase(candidate_abs)]
        )
    except ValueError:
        return False  # e.g. different drives on Windows
    return os.path.normcase(root_abs) == common


def build_path(root: Path | str, *components: Path | str) -> Path:
    """Join ``components`` under ``root``, refusing any escape.

    Raises :class:`PathOutsideRootError` when the joined candidate (after
    ``..`` normalization and symlink resolution) leaves ``root``. Pure path
    arithmetic — no directory is created.
    """
    root_path = Path(root)
    candidate = Path(os.path.normpath(root_path.joinpath(*components)))
    if not is_within(root_path, candidate):
        raise PathOutsideRootError(
            f"path {str(candidate)!r} escapes configured root {str(root_path)!r}"
        )
    return candidate


def _clean_extension(ext: str) -> str:
    cleaned = _NON_EXT_CHARS.sub("", ext).lower()
    if not cleaned:
        raise ValueError("extension must contain at least one alphanumeric character")
    return cleaned


def _format_published_at(value: date | datetime | str) -> str:
    if isinstance(value, str):
        value = value.strip()
        try:
            value = date.fromisoformat(value)
        except ValueError:
            try:
                value = datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"invalid published_at: {value!r}") from exc
    if not isinstance(value, (date, datetime)):
        raise ValueError(f"invalid published_at: {value!r}")
    return value.strftime("%Y-%m-%d")


def _absolute_real(path: Path | str) -> str:
    return os.path.realpath(os.path.abspath(os.fspath(path)))
