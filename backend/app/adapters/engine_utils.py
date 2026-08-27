"""Shared helpers for the engine parser adapters (f2 / parse-video-py)."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

__all__ = ["extension_of", "probe_file_size_mb"]

_UA = {"User-Agent": "Mozilla/5.0 (KoiFetch/0.1)"}
_EXT_UNSAFE = re.compile(r"[^a-z0-9]+")


def extension_of(url: str) -> str | None:
    """Lowercase alnum extension from the last path segment ('' when none)."""
    last = urlsplit(url).path.rsplit("/", 1)[-1]
    dot = last.rfind(".")
    if dot == -1 or not last[dot + 1 :]:
        return None
    cleaned = _EXT_UNSAFE.sub("", last[dot + 1 :].lower())
    return cleaned or None


def probe_file_size_mb(
    url: str,
    *,
    timeout_seconds: float,
    proxy: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> float | None:
    """Best-effort Content-Length of a media URL (GET, headers only).

    CDNs often reject HEAD or omit length; any failure yields ``None`` — the
    size is optional metadata, never a parse failure. ``transport`` is a test
    seam; ``None`` uses the real network.
    """
    if not url:
        return None
    kwargs: dict = {"timeout": timeout_seconds, "follow_redirects": True}
    if proxy:
        kwargs["proxy"] = proxy
    if transport is not None:
        kwargs["transport"] = transport
    try:
        with httpx.Client(**kwargs) as client:
            with client.stream("GET", url, headers=_UA) as response:
                length = response.headers.get("content-length")
        if length and length.isdigit():
            return round(int(length) / 1_000_000, 1)
    except Exception:  # probe is best-effort by contract
        pass
    return None
