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
  callback reports the complete byte count (progress 100). ``speed`` is the
  true per-interval figure — bytes/second since the previous callback (or
  since start for the first), matching the protocol contract.
* Returns a final :class:`DownloadResult` snapshot with ``status ==
  COMPLETED``, ``progress == 100`` and the total/speed figures (final speed
  is the overall average: total bytes / wall-clock). ``format`` and
  ``quality`` are copied from the command; ``save_to_nas``/``nas_path`` are
  intentionally ignored — v1 NAS saves go through the separate authenticated
  API (Task 10) using the storage adapter, never through the downloader.
* On failure (e.g. unwritable target) the adapter raises; the worker records
  the failed state. There is no retry logic here — retries are the worker's
  concern (``retry_count`` lives on the ORM row).

The size/chunk parameters exist only to keep tests fast; production settings
for a real engine will be engine-specific. ``total_bytes`` must be >= 1: a
zero-byte download would contradict the progress contract above (no chunk, so
no final 100% callback could ever fire).
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
        speed_limit_mb_s: float = 0.0,
    ) -> None:
        """Configure the stub's synthetic file size, chunking, and pacing.

        ``chunk_delay`` (seconds, default 0) sleeps between chunks to simulate
        slow network so progress speed is observable; keep it 0 in tests for
        speed. ``speed_limit_mb_s`` (0 = unlimited) is the factory-friendly
        alternative: it maps to the per-chunk delay that yields that average
        transfer rate (``chunk_size / (limit * 1e6)`` seconds). The two
        pacing knobs are mutually exclusive.
        """
        if total_bytes < 1:
            raise ValueError(f"total_bytes must be >= 1, got {total_bytes}")
        if chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
        if chunk_delay < 0:
            raise ValueError(f"chunk_delay must be >= 0, got {chunk_delay}")
        if speed_limit_mb_s < 0:
            raise ValueError(
                f"speed_limit_mb_s must be >= 0, got {speed_limit_mb_s}"
            )
        if speed_limit_mb_s > 0:
            if chunk_delay > 0:
                raise ValueError(
                    "specify only one of chunk_delay / speed_limit_mb_s"
                )
            chunk_delay = chunk_size / (speed_limit_mb_s * 1_000_000)
        self.total_bytes = total_bytes
        self.chunk_size = chunk_size
        self.chunk_delay = chunk_delay
        self.speed_limit_mb_s = speed_limit_mb_s

    def download(self, request: DownloadRequest) -> DownloadResult:
        target = Path(request.target_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        seed = self._byte_seed(request)
        total = self.total_bytes
        start = time.monotonic()
        last_time = start
        last_written = 0
        written = 0

        with target.open("wb") as out:
            while written < total:
                count = min(self.chunk_size, total - written)
                out.write((seed * ((count // _DIGEST_SIZE) + 1))[:count])
                written += count
                if self.chunk_delay:
                    time.sleep(self.chunk_delay)
                if request.progress_callback is not None:
                    now = time.monotonic()
                    elapsed = max(now - last_time, 1e-9)
                    interval_speed = (written - last_written) / elapsed
                    last_time = now
                    last_written = written
                    request.progress_callback(
                        DownloadProgress(
                            download_id=request.download_id,
                            status=DownloadStatus.DOWNLOADING,
                            progress=written / total * 100.0,
                            speed=interval_speed,
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
