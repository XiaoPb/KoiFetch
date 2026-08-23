"""Stub downloader adapter: writes deterministic local test media.

Implements :class:`app.adapters.protocols.DownloaderAdapter`. No network
access is ever performed: the stub materializes a small file of deterministic
bytes (derived from the download id + title) at the requested target path and
fires incremental :class:`~app.domain.models.DownloadProgress` callbacks as
chunks land, so the worker (Task 11) can exercise its full progress-persistence
path before real platform engines exist.

Behaviour contract (documented for the worker and later engines):

* Writes exactly ``total_bytes`` bytes to ``request.target_path``, creating
  parent directories. The byte stream is deterministic for a given
  ``download_id`` + ``title`` (SHA-256 digest repeated to fill each chunk),
  so re-downloading the same id reproduces the same file.
* Calls ``request.progress_callback`` (when set) once per chunk, in order,
  with ``status == DOWNLOADING`` snapshots whose ``downloaded_bytes`` grow
  monotonically and ``progress`` is ``downloaded / total * 100``. The final
  callback reports the complete byte count (progress 100). ``speed`` is
  bytes/second since the previous callback (or since start for the first).
* Returns a final :class:`DownloadResult` snapshot with ``status ==
  COMPLETED``, ``progress == 100`` and the total/speed figures. ``format`` and
  ``quality`` are copied from the command; ``save_to_nas``/``nas_path`` are
  intentionally ignored — v1 NAS saves go through the separate authenticated
  API (Task 10) using the storage adapter, never through the downloader.
* On failure (e.g. unwritable target) the adapter raises; the worker records
  the failed state. There is no retry logic here — retries are the worker's
  concern (``retry_count`` lives on the ORM row).

The size/chunk parameters exist only to keep tests fast; production settings
for a real engine will be engine-specific.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from app.adapters.protocols import DownloadRequest, DownloaderAdapter
from app.domain import DownloadProgress, DownloadResult, DownloadStatus

__all__ = ["StubDownloaderAdapter"]

_DEFAULT_TOTAL_BYTES = 1_048_576  # 1 MiB
_DEFAULT_CHUNK_SIZE = 64 * 1024  # 64 KiB
_DIGEST_SIZE = 32  # sha256


class StubDownloaderAdapter:
    """Deterministic, offline :class:`DownloaderAdapter` implementation."""

    def __init__(
        self,
        *,
        total_bytes: int = _DEFAULT_TOTAL_BYTES,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        chunk_delay: float = 0.0,
    ) -> None:
        """Configure the stub's synthetic file size and chunking.

        ``chunk_delay`` (seconds, default 0) simulates slow network so progress
        speed is observable; keep it 0 in tests for speed.
        """
        if total_bytes < 0:
            raise ValueError(f"total_bytes must be >= 0, got {total_bytes}")
        if chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
        if chunk_delay < 0:
            raise ValueError(f"chunk_delay must be >= 0, got {chunk_delay}")
        self._total_bytes = total_bytes
        self._chunk_size = chunk_size
        self._chunk_delay = chunk_delay

    def download(self, request: DownloadRequest) -> DownloadResult:
        target = Path(request.target_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        seed = self._byte_seed(request)
        total = self._total_bytes
        start = time.monotonic()
        written = 0

        with target.open("wb") as out:
            while written < total:
                count = min(self._chunk_size, total - written)
                out.write((seed * ((count // _DIGEST_SIZE) + 1))[:count])
                written += count
                if self._chunk_delay:
                    time.sleep(self._chunk_delay)
                if request.progress_callback is not None:
                    elapsed = max(time.monotonic() - start, 1e-9)
                    request.progress_callback(
                        DownloadProgress(
                            download_id=request.download_id,
                            status=DownloadStatus.DOWNLOADING,
                            progress=written / total * 100.0,
                            speed=written / elapsed,
                            downloaded_bytes=written,
                            total_bytes=total,
                        )
                    )

        elapsed = max(time.monotonic() - start, 1e-9)
        return DownloadResult(
            download_id=request.download_id,
            task_id=request.command.task_id,
            title=request.title,
            media_type=request.media_type,
            format=request.command.format,
            quality=request.command.quality,
            status=DownloadStatus.COMPLETED,
            progress=100.0,
            speed=total / elapsed,
            total_bytes=total,
            downloaded_bytes=total,
            retry_count=0,
            error_message=None,
        )

    @staticmethod
    def _byte_seed(request: DownloadRequest) -> bytes:
        """Deterministic 32-byte seed derived from download id + title."""
        material = f"{request.download_id}:{request.title or ''}"
        return hashlib.sha256(material.encode("utf-8")).digest()
