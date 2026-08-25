"""Real downloader adapter: HTTP streaming with byte-level progress (Tasks 7-8).

Implements :class:`app.adapters.protocols.DownloaderAdapter` for real media:
the video branch streams the media URL the parser resolved (stored in the
parse task's ``metadata_`` JSON as ``video_url``, handed over via
:attr:`app.adapters.protocols.DownloadRequest.metadata`) and fires
:class:`~app.domain.models.DownloadProgress` snapshots per chunk, honoring the
same contract as the stub downloader (monotonic ``downloaded_bytes``,
``progress`` = downloaded/total*100, interval ``speed``, final 100% callback).

Behaviour contract:

* **VIDEO** — requires ``request.metadata["video_url"]`` (engine-mode parse
  rows carry it; stub-era rows do not → typed
  :class:`~app.adapters.engine_errors.EngineDownloadError`). Streams with
  httpx, writing to ``request.target_path`` (parent dirs created), per-chunk
  progress callbacks, returns a ``COMPLETED`` :class:`DownloadResult`.
* **MUSIC** — stubbed in Task 7 (raises a typed "not wired yet" error) and
  implemented in Task 8: reads ``request.metadata["song_info"]`` (a persisted
  :class:`musicdl.SongInfo`-compatible dict, ``SongInfo.fromdict``-able).
  Plain ``HTTP`` tracks are streamed directly from ``download_url`` with real
  progress; ``HLS``/encrypted tracks are delegated to ``musicdl.MusicClient``
  into a per-download staging dir, then moved out of staging to
  ``target_path`` and the staging dir removed (coarse progress: one 0%
  snapshot, then the completed result — musicdl owns its own progress
  internally and exposes no callback).
* **Errors** — httpx/requests failures translate to the typed
  :mod:`app.adapters.engine_errors` hierarchy (403 → PlatformBlockedError,
  timeouts → EngineTimeoutError, connect → EngineNetworkError). The worker
  removes the partial target on failure; the adapter leaves cleanup to it.
* **``transport`` is a test seam** — production ``None`` (real network);
  tests inject ``httpx.MockTransport``.
"""

from __future__ import annotations

import shutil  # used by the Task 8 music branch (staging move/cleanup)
import time
from pathlib import Path  # used by the Task 8 music branch

import httpx
from musicdl import musicdl as _musicdl  # consumed by the Task 8 music branch

from app.adapters.engine_errors import (
    EngineDownloadError,
    EngineError,
    translate_engine_exception,
)
from app.adapters.protocols import DownloadRequest, DownloaderAdapter
from app.domain import DownloadProgress, DownloadResult, DownloadStatus, MediaType

__all__ = ["EngineDownloaderAdapter"]

_MESSAGE_MISSING_MEDIA = "缺少媒体地址，无法下载 / Missing media URL"
_MESSAGE_MEDIA_TYPE = "该引擎暂不支持此媒体类型 / Media type not supported by the engine yet"
_MESSAGE_MUSIC_NOT_WIRED = (
    "音乐下载暂未接入（Task 8 实现） / Music download not wired yet (Task 8)"
)

_UA = {"User-Agent": "Mozilla/5.0 (KoiFetch/0.1)"}
_DEFAULT_CHUNK_SIZE = 64 * 1024
# musicdl source client names used by the Task 8 music branch (the five
# Mainland-China defaults musicdl ships with).
_DEFAULT_MUSIC_SOURCES = [
    "MiguMusicClient", "NeteaseMusicClient", "QQMusicClient",
    "KuwoMusicClient", "QianqianMusicClient",
]


class EngineDownloaderAdapter:
    """Real :class:`DownloaderAdapter`: streams media with progress callbacks."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        download_timeout_seconds: float = 30.0,
        proxy: str | None = None,
        music_sources: list[str] | None = None,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._download_timeout = download_timeout_seconds
        self._proxy = proxy
        self._music_sources = music_sources or list(_DEFAULT_MUSIC_SOURCES)
        self._chunk_size = chunk_size
        self._transport = transport  # test seam; None = real network

    # -- protocol ----------------------------------------------------------

    def download(self, request: DownloadRequest) -> DownloadResult:
        if request.media_type is MediaType.VIDEO:
            return self._download_video(request)
        if request.media_type is MediaType.MUSIC:
            return self._download_music(request)
        raise EngineDownloadError(_MESSAGE_MEDIA_TYPE)

    # -- video -------------------------------------------------------------

    def _download_video(self, request: DownloadRequest) -> DownloadResult:
        url = request.metadata.get("video_url") if request.metadata else None
        if not url:
            raise EngineDownloadError(_MESSAGE_MISSING_MEDIA)
        return self._stream_to_target(request, url, total_hint=None)

    # -- music -------------------------------------------------------------
    # Implemented in Task 8 (real download + progress via musicdl). The
    # constructor already accepts ``music_sources`` so the factory contract is
    # stable; until Task 8 the branch fails with a typed, honest error.

    def _download_music(self, request: DownloadRequest) -> DownloadResult:
        raise EngineDownloadError(_MESSAGE_MUSIC_NOT_WIRED)

    # -- shared streaming core ---------------------------------------------

    def _stream_to_target(
        self,
        request: DownloadRequest,
        url: str,
        *,
        total_hint: int | None = None,
        headers: dict | None = None,
    ) -> DownloadResult:
        target = Path(request.target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {
            "timeout": self._download_timeout,
            "follow_redirects": True,
        }
        if self._proxy:
            kwargs["proxy"] = self._proxy
        if self._transport is not None:
            kwargs["transport"] = self._transport
        request_headers = dict(_UA)
        if headers:
            request_headers.update(headers)

        start = time.monotonic()
        last_time = start
        last_written = 0
        written = 0
        total = total_hint
        try:
            with httpx.Client(**kwargs) as client:
                with client.stream("GET", url, headers=request_headers) as response:
                    response.raise_for_status()  # 403 -> HTTPStatusError -> typed
                    if total is None:
                        length = response.headers.get("content-length")
                        total = int(length) if length and length.isdigit() else None
                    with target.open("wb") as out:
                        for chunk in response.iter_bytes(chunk_size=self._chunk_size):
                            out.write(chunk)
                            written += len(chunk)
                            if request.progress_callback is not None:
                                now = time.monotonic()
                                interval = max(now - last_time, 1e-9)
                                request.progress_callback(
                                    DownloadProgress(
                                        download_id=request.download_id,
                                        status=DownloadStatus.DOWNLOADING,
                                        progress=(
                                            written / total * 100.0 if total else 0.0
                                        ),
                                        speed=(written - last_written) / interval,
                                        downloaded_bytes=written,
                                        total_bytes=total,
                                    )
                                )
                                last_time = now
                                last_written = written
        except EngineError:
            raise
        except Exception as exc:
            raise translate_engine_exception(exc, url=url, operation="download") from exc

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
            speed=written / elapsed,
            total_bytes=total or written,
            downloaded_bytes=written,
            retry_count=0,
            error_message=None,
        )
