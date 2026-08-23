"""Safe filename and path helpers for the domain layer.

Everything that turns untrusted input (platform titles, author names, source
ids, extensions) into filesystem names or paths lives here. The PRD requires
that path segments be *slugified* — restricted to letters, digits, ``-`` and
``_``, capped at ~120 characters — so that hostile titles cannot traverse or
overwrite outside the configured storage roots (``../``, absolute paths, null
bytes, reserved characters are all neutralized).

**Charset decision (user-confirmed): Chinese + English, Chinese-primary.**
Slugs preserve Unicode word characters — CJK letters/digits are kept intact
(``slugify("晴天 示例") == "晴天-示例"``), while every run of other characters
(spaces, punctuation, ``-``, ``_``, slashes, control chars) collapses to a
single ``-``. Accented Latin decomposes first (``café`` -> ``cafe``). Only when
*nothing* word-like remains does the ``fallback`` (default ``"untitled"``)
appear. The ``fallback`` is itself slugified so a caller-supplied value can
never reintroduce traversal.

Two layers of defense are provided:

* :func:`slugify` / :func:`safe_media_filename` produce safe *names* — the
  filename pattern per PRD §3.3.6 (``<published_at>_<artist?>_<title_slug>
  _<source_id>.<ext>`` for video/music — the optional ``artist`` segment is
  inserted when provided — and numbered ``<index:03d>_<title_slug>.<ext>`` for
  images).
* :func:`is_within` / :func:`build_path` guarantee *containment* — a candidate
  path may not escape a configured root, even through symlinks. This is the
  final gate: callers compose slugs into components, and ``build_path`` rejects
  anything that resolves outside the root (raising
  :class:`PathOutsideRootError`).

Design notes (documented decisions):

* **NFKD normalization** decomposes accented Latin and full-width forms before
  the word filter (CJK is unaffected — it has no decomposition).
* **Truncation with a hash suffix.** When the slug exceeds ``max_length`` it is
  truncated and a stable 8-hex sha256 suffix of the *original* (NFKD) text is
  appended, so distinct long titles never collide. The hash input is encoded
  with ``errors="replace"`` so a lone surrogate (e.g. a JSON ``\\ud800``
  escape) cannot raise ``UnicodeEncodeError``. When ``max_length`` is too small
  to fit the suffix it is truncated without one (caller-chosen tiny limits;
  keep ``max_length >= 9`` for uniqueness).
* **Containment resolves symlinks.** ``is_within`` compares realpaths (via
  ``os.path.realpath``) of the absolute forms of both paths, using
  ``os.path.normcase`` so Windows drive/case differences are handled and
  ``os.path.commonpath`` so prefix look-alikes (``C:\\a`` vs ``C:\\ab``) can
  never pass. Both inputs may be relative; they are resolved against the
  current working directory.
* **``build_path`` requires absolute roots.** A relative root would silently
  resolve against the caller's CWD and make containment meaningless, so it
  raises ``ValueError``. Note the configured storage roots in
  ``app.infrastructure.config`` are currently *relative* (``data/pond/...``);
  the Task 6 storage adapter must resolve them to absolute paths at startup
  before calling this helper.
* **TOCTOU handoff.** ``build_path`` validates containment at *build* time
  only. The Task 6 StorageAdapter must re-verify containment at open/write
  time (roots may be swapped, symlinks may appear between build and use).
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
# Every run of non-word characters (Unicode-aware) collapses to '-'; this also
# swallows '-', '_', slashes, dots, control chars and lone surrogates.
_NON_SLUG_CHARS = re.compile(r"[\W_]+")
_NON_EXT_CHARS = re.compile(r"[^a-zA-Z0-9]+")


class PathOutsideRootError(ValueError):
    """Raised when a candidate path escapes its configured storage root."""


def slugify(
    text: object,
    max_length: int = DEFAULT_SLUG_MAX_LENGTH,
    fallback: str = "untitled",
) -> str:
    """Turn untrusted text into a safe path segment.

    The result contains only Unicode word characters (letters and digits of
    any script — CJK included) and ``-``: text is NFKD-normalized, every run
    of non-word characters collapses to a single ``-``, the result is stripped
    of leading/trailing separators and ASCII-lowercased. A slug that is empty
    after filtering returns ``fallback`` (itself slugified and length-clamped).

    Slugs longer than ``max_length`` are truncated and given a stable 8-hex
    sha256 suffix of the source text, keeping the total within ``max_length``
    (see module docstring for the tiny-limit edge case and surrogate safety).
    """
    if max_length < 1:
        raise ValueError("max_length must be >= 1")
    if not isinstance(text, str):
        text = str(text)
    normalized = unicodedata.normalize("NFKD", text)
    slug = _NON_SLUG_CHARS.sub("-", normalized).strip("-").lower()
    if not slug:
        return _clean_fallback(fallback, max_length)
    if len(slug) <= max_length:
        return slug
    if max_length < _HASH_SUFFIX_LENGTH + 2:
        return slug[:max_length]
    digest = hashlib.sha256(
        normalized.encode("utf-8", errors="replace")
    ).hexdigest()[:_HASH_SUFFIX_LENGTH]
    head = slug[: max_length - _HASH_SUFFIX_LENGTH - 1].rstrip("-")
    return f"{head}-{digest}"


def safe_media_filename(
    media_type: MediaType,
    *,
    published_at: date | datetime | str | None = None,
    title: str,
    artist: str | None = None,
    source_id: str | None = None,
    ext: str,
    index: int | None = None,
) -> str:
    """Build a PRD-style media filename with every component slugified.

    * video/music: ``<published_at>_<artist?>_<title_slug>_<source_id>.<ext>``
      (``artist`` is PRD §3.3.6's music pattern segment; inserted only when
      provided, so the plain video/music pattern is unchanged).
    * image: ``<index:03d>_<title_slug>.<ext>`` (``published_at`` is unused).

    ``published_at`` may be a ``date``, ``datetime`` or ISO-8601 string and is
    normalized to ``YYYY-MM-DD``; it is required for video/music only. ``ext``
    is sanitized to ``[a-z0-9]+`` (any non-alphanumeric characters, including
    dots and slashes, are removed and the result lowercased) so an extension
    can never smuggle path separators. Only the returned name is produced here
    — no filesystem access; directory building under a root is the caller's
    job via :func:`build_path`.
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
    if published_at is None:
        raise ValueError("published_at is required for video/music filenames")
    published = _format_published_at(published_at)
    components = [published]
    if artist:
        components.append(slugify(artist))
    components.extend([slugify(title), slugify(source_id)])
    return f"{'_'.join(components)}.{ext}"


def is_within(root: Path | str, candidate: Path | str) -> bool:
    """Return ``True`` when ``candidate`` resolves inside ``root``.

    Both paths are made absolute and symlinks resolved (``os.path.realpath``)
    before comparison, so ``../`` escapes and symlink escapes are detected.
    ``candidate == root`` counts as within. Returns ``False`` (never raises)
    for disjoint roots such as different Windows drives. Relative inputs are
    resolved against the current working directory — prefer absolute paths.
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
    """Join ``components`` under an *absolute* ``root``, refusing any escape.

    Raises :class:`ValueError` when ``root`` is not absolute (a relative root
    would silently resolve against the caller's CWD) and
    :class:`PathOutsideRootError` when the joined candidate (after ``..``
    normalization and symlink resolution) leaves ``root``. Pure path
    arithmetic — no directory is created. See the module docstring for the
    TOCTOU note: containment is validated here at build time only.
    """
    root_path = Path(root)
    if not root_path.is_absolute():
        raise ValueError(
            f"root must be an absolute path (got {str(root_path)!r}); "
            "resolve configured storage roots to absolute at startup"
        )
    candidate = Path(os.path.normpath(root_path.joinpath(*components)))
    if not is_within(root_path, candidate):
        raise PathOutsideRootError(
            f"path {str(candidate)!r} escapes configured root {str(root_path)!r}"
        )
    return candidate


def _clean_fallback(fallback: object, max_length: int) -> str:
    if not isinstance(fallback, str) or not fallback:
        raise ValueError("fallback must be a non-empty string")
    cleaned = _NON_SLUG_CHARS.sub("-", fallback).strip("-").lower()
    if not cleaned:
        raise ValueError("fallback must contain at least one word character")
    return cleaned[:max_length]


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
