"""Tests for the engine downloader adapter (Tasks 7-8): real HTTP streaming
with byte-level progress (video), and the musicdl SongInfo path (music).
All downloads run through httpx.MockTransport or a faked MusicClient — the
suite never touches the network."""

import httpx
import pytest

from app.adapters.downloader_engine import EngineDownloaderAdapter
from app.adapters.engine_errors import (
    EngineDownloadError,
    EngineNetworkError,
    EngineTimeoutError,
    PlatformBlockedError,
)
from app.adapters.protocols import DownloadRequest
from app.domain import (
    DownloadCommand,
    DownloadProgress,
    DownloadStatus,
    MediaType,
)

TASK_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
DOWNLOAD_ID = "33333333-3333-3333-3333-333333333333"
_VIDEO_METADATA = {"video_url": "https://cdn.example/v.mp4"}
_MUSIC_METADATA = {
    "song_info": {
        "song_name": "晴天", "singers": "周杰伦", "ext": "mp3",
        "file_size_bytes": 1024, "identifier": "id1",
        "protocol": "HTTP", "download_url": "https://cdn.example/song.mp3",
        "default_download_headers": {}, "work_dir": "./",
    }
}


def _request(tmp_path, *, metadata=None, media_type=MediaType.VIDEO, progress_callback=None):
    return DownloadRequest(
        command=DownloadCommand(task_id=TASK_ID),
        download_id=DOWNLOAD_ID,
        target_path=tmp_path / "out" / "media.bin",
        title="demo",
        media_type=media_type,
        source_url="https://v.douyin.com/abc/",
        metadata=metadata or _VIDEO_METADATA,
        progress_callback=progress_callback,
    )


def _adapter(*, payload: bytes = b"x" * 1000, status: int = 200) -> EngineDownloaderAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if status >= 400:
            return httpx.Response(status, request=request, content=b"")
        return httpx.Response(
            status, request=request, content=payload,
            headers={"content-length": str(len(payload))},
        )

    return EngineDownloaderAdapter(transport=httpx.MockTransport(handler))


class TestVideoDownload:
    def test_streams_media_to_target_with_progress(self, tmp_path):
        seen: list[DownloadProgress] = []
        adapter = _adapter(payload=b"z" * 500)
        result = adapter.download(_request(tmp_path, progress_callback=seen.append))

        assert (tmp_path / "out" / "media.bin").read_bytes() == b"z" * 500
        assert result.status is DownloadStatus.COMPLETED
        assert result.progress == 100.0
        assert result.downloaded_bytes == 500
        assert result.total_bytes == 500
        assert result.task_id == TASK_ID
        assert result.download_id == DOWNLOAD_ID
        assert result.media_type is MediaType.VIDEO
        assert seen, "progress callback must fire"
        assert seen[-1].downloaded_bytes == 500
        assert all(p.status is DownloadStatus.DOWNLOADING for p in seen)
        assert all(p.speed is not None and p.speed >= 0 for p in seen)

    def test_missing_video_url_raises_typed_error(self, tmp_path):
        adapter = _adapter()
        with pytest.raises(EngineDownloadError):
            adapter.download(_request(tmp_path, metadata={"stub": True}))

    def test_403_maps_to_platform_blocked(self, tmp_path):
        adapter = _adapter(status=403)
        with pytest.raises(PlatformBlockedError):
            adapter.download(_request(tmp_path))

    def test_network_error_maps_to_typed_error(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        adapter = EngineDownloaderAdapter(transport=httpx.MockTransport(handler))
        with pytest.raises(EngineNetworkError):
            adapter.download(_request(tmp_path))

    def test_unknown_media_type_raises_typed_error(self, tmp_path):
        adapter = _adapter()
        with pytest.raises(EngineDownloadError):
            adapter.download(_request(tmp_path, media_type=MediaType.IMAGE))

    def test_truncated_body_is_not_reported_completed(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request, content=b"x" * 100,
                                  headers={"content-length": "1000"})

        adapter = EngineDownloaderAdapter(transport=httpx.MockTransport(handler))
        with pytest.raises(EngineDownloadError):
            adapter.download(_request(tmp_path))

    def test_download_without_content_length(self, tmp_path):
        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                yield b"abc"

            def close(self):
                pass

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request, stream=Stream())

        seen: list[DownloadProgress] = []
        adapter = EngineDownloaderAdapter(transport=httpx.MockTransport(handler))
        result = adapter.download(_request(tmp_path, progress_callback=seen.append))
        assert result.status is DownloadStatus.COMPLETED
        assert result.downloaded_bytes == 3
        assert result.total_bytes == 3  # total or written (length unknown)
        assert (tmp_path / "out" / "media.bin").read_bytes() == b"abc"
        # absent-length contract: callbacks report 0.0 progress and no total
        assert seen and all(p.progress == 0.0 for p in seen)
        assert all(p.total_bytes is None for p in seen)

    def test_progress_monotonic_across_chunks(self, tmp_path):
        seen: list[DownloadProgress] = []
        # > 64 KiB chunk size yields multiple chunks (httpx chunks at
        # chunk_size), so monotonicity/interval speed are exercised.
        adapter = _adapter(payload=b"x" * (200 * 1024))
        adapter.download(_request(tmp_path, progress_callback=seen.append))
        assert len(seen) >= 2
        bytes_seen = [p.downloaded_bytes for p in seen]
        assert bytes_seen == sorted(bytes_seen)
        assert seen[-1].downloaded_bytes == 200 * 1024

    def test_mid_stream_error_maps_to_typed_error(self, tmp_path):
        class RaisingStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b"partial"
                raise httpx.ReadError(
                    "reset", request=httpx.Request("GET", "https://cdn.example/v.mp4")
                )

            def close(self):
                pass

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, request=request,
                headers={"content-length": "100"},
                stream=RaisingStream(),
            )

        adapter = EngineDownloaderAdapter(transport=httpx.MockTransport(handler))
        with pytest.raises(EngineNetworkError):
            adapter.download(_request(tmp_path))

    def test_500_maps_to_engine_download_error(self, tmp_path):
        adapter = _adapter(status=500)
        with pytest.raises(EngineDownloadError):
            adapter.download(_request(tmp_path))

    def test_timeout_maps_to_engine_timeout_error(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        adapter = EngineDownloaderAdapter(transport=httpx.MockTransport(handler))
        with pytest.raises(EngineTimeoutError):
            adapter.download(_request(tmp_path))


class TestMusicPlaceholder:
    def test_music_branch_raises_not_wired_yet(self, tmp_path):
        adapter = _adapter()
        with pytest.raises(EngineDownloadError, match="not wired yet"):
            adapter.download(
                _request(tmp_path, metadata=_MUSIC_METADATA, media_type=MediaType.MUSIC)
            )
