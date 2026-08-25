"""Tests for the engine downloader adapter (Tasks 7-8): real HTTP streaming
with byte-level progress (video), and the musicdl SongInfo path (music).
All downloads run through httpx.MockTransport or a faked MusicClient — the
suite never touches the network."""

import httpx
import pytest

import app.adapters.downloader_engine as downloader_engine
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
