"""Duration formatting helpers for the domain layer.

The PRD displays durations as ``"MM:SS"`` strings (e.g. ``"03:20"``, §3.1.4)
while the ORM stores integer seconds (``parse_tasks.duration``, Task 4).
These two functions are the conversion boundary so application services and
the worker never hand-parse either representation.

Conventions:

* ``format_duration`` always emits two-digit minutes and seconds; minutes may
  exceed 59 (a 1:01:01 video is ``"61:01"``) — the PRD defines no hours
  component, and MM:SS is the common convention for media players.
* ``parse_duration`` accepts one- or two-digit minutes but requires
  two-digit seconds in 00-59, rejecting everything else (so ``"03:99"`` and
  ``"3:2"`` fail loudly instead of silently mis-reading).
"""

from __future__ import annotations

import re

__all__ = ["format_duration", "parse_duration"]

_DURATION_RE = re.compile(r"^(\d+):([0-5]\d)$")


def format_duration(seconds: int) -> str:
    """Format integer seconds as ``MM:SS`` (minutes may exceed 59)."""
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise TypeError(f"seconds must be an int, got {type(seconds).__name__}")
    if seconds < 0:
        raise ValueError(f"seconds must be >= 0, got {seconds}")
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def parse_duration(text: str) -> int:
    """Parse ``MM:SS`` (minutes 1+ digits, seconds exactly 2 digits) to seconds.

    Raises :class:`ValueError` for malformed input and :class:`TypeError` for
    non-string input.
    """
    if not isinstance(text, str):
        raise TypeError(f"duration must be a str, got {type(text).__name__}")
    match = _DURATION_RE.fullmatch(text.strip())
    if not match:
        raise ValueError(f"invalid duration (expected MM:SS): {text!r}")
    minutes, seconds = (int(part) for part in match.groups())
    return minutes * 60 + seconds
