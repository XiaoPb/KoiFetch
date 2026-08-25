"""Real downloader adapter: HTTP streaming with byte-level progress for video
and music.

Implements :class:`app.adapters.protocols.DownloaderAdapter` for real media:
the video branch streams the media URL the parser resolved (stored in the
parse task's ``metadata_`` JSON as ``video_url``, handed over via
:attr:`app.adapters.protocols.DownloadRequest.metadata`) and fires
:class:`~app.domain.models.DownloadProgress` snapshots per chunk, honoring the
same contract as the stub downloader (monotonic ``downloaded_bytes``,
``progress`` = downloaded/total*100, interval ``speed``). The final callback
reports the complete byte count when content-length is known; without it
callbacks report 0.0 progress and the COMPLETED result reconciles the totals.

Behaviour contract:

* **VIDEO** — requires ``request.metadata["video_url"]`` (engine-mode parse
  rows carry it; stub-era rows do not → typed
  :class:`~app.adapters.engine_errors.EngineDownloadError`). Streams with
  httpx, writing to ``request.target_path`` (parent dirs created), per-chunk
  progress callbacks, returns a ``COMPLETED`` :class:`DownloadResult`.
* **MUSIC** — reads ``request.metadata["song_info"]`` (a persisted
  :class:`musicdl.SongInfo`-compatible dict, ``SongInfo.fromdict``-able;
  missing/not-a-dict → typed
  :class:`~app.adapters.engine_errors.EngineDownloadError`). Plain ``HTTP``
  tracks stream directly from ``download_url`` and need only a usable URL.
  ``HLS``/encrypted tracks are delegated to ``musicdl.MusicClient`` into a
  per-download staging dir, then moved out of staging to ``target_path``
  before the staging dir is removed (coarse progress: one 0% snapshot, then
  the completed result — musicdl exposes no callback); that delegated path
  requires ``song_info`` to carry ``source`` (one of the configured
  ``music_sources``, else a typed "not configured" error), a valid ``ext``,
  and a usable ``download_url``/``download_url_status`` — missing pieces fail
  with typed errors.
* **Errors** — httpx/requests failures translate to the typed
  :mod:`app.adapters.engine_errors` hierarchy (403 → PlatformBlockedError,
  timeouts → EngineTimeoutError, connect → EngineNetworkError). Music-engine
  failures mostly collapse to the stable "下载失败 / Download failed" (musicdl
  swallows its own timeouts/403s into empty results), and the staging-move
  step is translated the same way — no raw filesystem paths or engine text
  reach clients. The worker removes the partial target on failure; the
  adapter always removes its own staging dir.
* **``transport`` is a test seam** — production ``None`` (real network);
  tests inject ``httpx.MockTransport``.
* **Dependency** — importing this module requires ``musicdl`` (installed via
  ``backend/requirements.txt``; engine mode is its only consumer). The
  top-level ``from musicdl import musicdl`` import is deliberate and
  documented: only engine mode needs it.
"""

from __future__ import annotations

import shutil  # used by the music branch (staging move/cleanup)
import time
from pathlib import Path  # used by the music branch

import httpx
from musicdl import musicdl as _musicdl  # consumed by the music branch

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
_MESSAGE_MISSING_SONG = "缺少音乐信息，无法下载 / Missing song info"
_MESSAGE_DOWNLOAD_FAILED = "下载失败 / Download failed"
_MESSAGE_MUSIC_SOURCE = "音乐来源未配置 / Music source not configured"
_MESSAGE_INCOMPLETE = "下载不完整 / Incomplete download"

_UA = {"User-Agent": "Mozilla/5.0 (KoiFetch/0.1)"}
_DEFAULT_CHUNK_SIZE = 64 * 1024
# musicdl source client names used by the music branch (the five
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
    # Real download + progress via musicdl: plain HTTP tracks are
    # streamed directly through the shared core; HLS/encrypted tracks go
    # through ``musicdl.MusicClient`` into a staging dir, then are moved out.

    def _download_music(self, request: DownloadRequest) -> DownloadResult:
        song = request.metadata.get("song_info") if request.metadata else None
        if not isinstance(song, dict):
            raise EngineDownloadError(_MESSAGE_MISSING_SONG)
        info = _musicdl.SongInfo.fromdict(song)
        protocol = (info.protocol or "HTTP").upper()
        if (
            protocol == "HTTP"
            and isinstance(info.download_url, str)
            and info.download_url.startswith("http")
        ):
            return self._stream_to_target(
                request, info.download_url,
                total_hint=info.file_size_bytes,
                headers=info.default_download_headers or None,
            )
        return self._download_music_via_engine(request, info)

    def _download_music_via_engine(
        self, request: DownloadRequest, info: "_musicdl.SongInfo"
    ) -> DownloadResult:
        """Delegate HLS/encrypted tracks to musicdl, then move the file.

        Ordering is load-bearing: the downloaded file lives INSIDE the
        staging dir, so it is moved out BEFORE the staging dir is removed.
        """
        if info.source not in self._music_sources:
            raise EngineDownloadError(_MESSAGE_MUSIC_SOURCE)
        staging = request.target_path.parent / f".musicdl-{request.download_id[:8]}"
        info.work_dir = str(staging)
        client = _musicdl.MusicClient(
            music_sources=self._music_sources,
            init_music_clients_cfg={
                source: {"work_dir": str(staging)} for source in self._music_sources
            },
            requests_overrides={
                source: {"timeout": (self._timeout, self._download_timeout)}
                for source in self._music_sources
            },
        )
        if request.progress_callback is not None:
            request.progress_callback(
                DownloadProgress(
                    download_id=request.download_id,
                    status=DownloadStatus.DOWNLOADING,
                    progress=0.0,
                    speed=None,
                    downloaded_bytes=0,
                    total_bytes=info.file_size_bytes,
                )
            )
        try:
            downloaded = client.download(song_infos=[info])
            if not downloaded:
                raise EngineDownloadError(_MESSAGE_DOWNLOAD_FAILED)
            saved = Path(downloaded[0].save_path)
            shutil.move(str(saved), request.target_path)
        except EngineError:
            raise
        except Exception as exc:
            raise translate_engine_exception(
                exc, url=info.download_url or "", operation="download"
            ) from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        written = request.target_path.stat().st_size
        return DownloadResult(
            download_id=request.download_id,
            task_id=request.command.task_id,
            title=request.title,
            media_type=request.media_type,
            format=request.command.format,
            quality=request.command.quality,
            status=DownloadStatus.COMPLETED,
            progress=100.0,
            speed=None,
            total_bytes=written,
            downloaded_bytes=written,
            retry_count=0,
            error_message=None,
        )

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
            target.parent.mkdir(parents=True, exist_ok=True)
            with httpx.Client(**kwargs) as client:
                with client.stream("GET", url, headers=request_headers) as response:
                    response.raise_for_status()  # 403 -> HTTPStatusError -> typed
                    # The server-declared length is authoritative over any
                    # metadata hint (hints may be stale/approximate); the
                    # hint only fills in when the server sends no length.
                    length = response.headers.get("content-length")
                    if length and length.isdigit():
                        total = int(length)
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

        if total is not None and written != total:
            raise EngineDownloadError(_MESSAGE_INCOMPLETE)

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
