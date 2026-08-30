"""Unit tests for the preview application service (Task 8): direct calls,
no HTTP layer.

Covers the use case that ``GET /api/preview/{task_id}`` delegates to: loading
a :class:`ParseTask` by id and building the v1 metadata-only preview dict
(preview_type from media_type, duration reformatted to MM:SS, qualities/
bitrates read back from the persisted metadata JSON, and the ``streams``
ladder), the 3001 任务不存在 failure for a well-formed but unknown task_id,
and the ``None``/``[]`` fallbacks for rows whose metadata lacks the enriched
keys (older rows or future engines that skip the parse-service enrichment).
"""

import io
import logging
import uuid
import zipfile

import httpx
import pytest

from app.adapters.safe_upstream import SafeUpstreamClient, UpstreamStream
from app.api.responses import CODE_BAD_REQUEST, CODE_TASK_NOT_FOUND, ApiError
from app.application.preview_service import PreviewService
from app.domain import MediaType
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import ParseTask


def make_task_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'preview-service.db'}")
    Base.metadata.create_all(engine)
    return engine


def _seed_task(engine, *, task_id, url, media_type, format, title="t", duration=323,
               metadata=None) -> None:
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=task_id,
                url=url,
                platform="bilibili" if media_type is MediaType.VIDEO else "example",
                media_type=media_type,
                title=title,
                cover_url="https://cdn.example.com/cover.jpg",
                duration=duration,
                format=format,
                metadata_=metadata or {},
            )
        )


def _seed_media_task(engine, *, task_id, media_type=MediaType.VIDEO, metadata=None,
                     title="t") -> None:
    """Seed a minimal media task row (defaults: bilibili video, mp4)."""
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=task_id,
                url="https://example.com/source",
                platform="bilibili" if media_type is MediaType.VIDEO else "xiaohongshu",
                media_type=media_type,
                title=title,
                format="mp4",
                metadata_=metadata or {},
            )
        )


class TestPreviewService:
    def test_preview_existing_task_returns_metadata_dict(self, engine):
        task_id = make_task_id()
        _seed_task(
            engine,
            task_id=task_id,
            url="https://www.bilibili.com/video/av123",
            media_type=MediaType.VIDEO,
            format="mp4",
            title="av123",
            metadata={
                "file_size_mb": 12.5,
                "available_qualities": ["1080p", "720p", "480p"],
                "available_bitrates": [],
            },
        )
        data = PreviewService(engine=engine).preview(task_id)
        assert data["task_id"] == task_id
        assert data["preview_type"] == "video"
        assert data["url"] == "https://www.bilibili.com/video/av123"
        assert data["platform"] == "bilibili"
        assert data["title"] == "av123"
        assert data["cover"] is None  # private legacy cover is never public
        assert data["duration"] == "05:23"  # 323s → MM:SS
        assert data["format"] == "mp4"
        assert data["file_size_mb"] == 12.5
        assert data["available_qualities"] == ["1080p", "720p", "480p"]
        assert data["streams"] == [
            {"quality": "1080p", "format": "mp4"},
            {"quality": "720p", "format": "mp4"},
            {"quality": "480p", "format": "mp4"},
        ]

    def test_preview_missing_task_raises_3001(self, engine):
        with pytest.raises(ApiError) as excinfo:
            PreviewService(engine=engine).preview(make_task_id())
        assert excinfo.value.http_status == 400
        assert excinfo.value.code == CODE_TASK_NOT_FOUND

    def test_preview_task_without_enriched_metadata_uses_fallbacks(self, engine):
        task_id = make_task_id()
        _seed_task(
            engine,
            task_id=task_id,
            url="https://music.example.com/song/hello.mp3",
            media_type=MediaType.MUSIC,
            format="mp3",
            title="hello",
            metadata={"stub": True},
        )
        data = PreviewService(engine=engine).preview(task_id)
        assert data["preview_type"] == "music"
        assert data["file_size_mb"] is None
        assert data["available_qualities"] == []
        assert data["available_bitrates"] == []
        assert data["streams"] == []

    def test_preview_image_task_has_no_streams(self, engine):
        task_id = make_task_id()
        _seed_task(
            engine,
            task_id=task_id,
            url="https://www.xiaohongshu.com/photo/cover.jpg",
            media_type=MediaType.IMAGE,
            format="jpg",
            title="cover",
        )
        data = PreviewService(engine=engine).preview(task_id)
        assert data["preview_type"] == "image"
        assert data["streams"] == []


VIDEO_TASK_ID = "11111111-1111-1111-1111-111111111111"
IMAGE_TASK_ID = "22222222-2222-2222-2222-222222222222"


class TestVideoStreamProxy:
    def test_private_manifest_url_is_translated_to_api_error(self, engine):
        service = PreviewService(
            engine=engine,
            upstream=SafeUpstreamClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=b"must-not-fetch")
                )
            ),
        )
        _seed_media_task(
            engine,
            task_id=VIDEO_TASK_ID,
            metadata={"video_url": "http://127.0.0.1/manifest.m3u8"},
        )
        with pytest.raises(ApiError) as exc_info:
            service.stream_video(VIDEO_TASK_ID, range_header=None)
        assert exc_info.value.code == CODE_BAD_REQUEST

    def test_stream_video_proxies_bytes_and_passes_range(self, engine):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("user-agent") == "Mozilla/5.0 (KoiFetch/0.1)"
            if request.headers.get("range"):
                return httpx.Response(
                    206,
                    headers={
                        "content-type": "video/mp4",
                        "content-range": "bytes 0-99/200",
                    },
                    content=b"x" * 100,
                )
            return httpx.Response(
                200,
                headers={"content-type": "video/mp4", "content-length": "200"},
                content=b"x" * 200,
            )

        service = PreviewService(
            engine=engine,
            transport=httpx.MockTransport(handler),
        )
        _seed_media_task(
            engine,
            task_id=VIDEO_TASK_ID,
            metadata={"video_url": "https://cdn.example.com/v.mp4"},
        )
        stream = service.stream_video(VIDEO_TASK_ID, range_header="bytes=0-99")
        assert stream.status_code == 206
        assert stream.content_type == "video/mp4"
        assert stream.headers["Content-Range"] == "bytes 0-99/200"
        assert b"".join(stream.chunks) == b"x" * 100
        stream.close()

    def test_stream_video_rejects_hls(self, engine):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/vnd.apple.mpegurl"},
                content=b"#EXTM3U\n",
            )

        service = PreviewService(engine=engine, transport=httpx.MockTransport(handler))
        _seed_media_task(
            engine,
            task_id=VIDEO_TASK_ID,
            metadata={"video_url": "https://cdn.example.com/playlist.m3u8"},
        )
        with pytest.raises(Exception) as exc_info:
            service.stream_video(VIDEO_TASK_ID, range_header=None)
        api_error = exc_info.value
        assert getattr(api_error, "code", None) == CODE_BAD_REQUEST

    def test_stream_video_unknown_task_raises_3001(self, engine):
        service = PreviewService(engine=engine, transport=httpx.MockTransport(handler=None))
        with pytest.raises(Exception) as exc_info:
            service.stream_video("99999999-9999-9999-9999-999999999999", range_header=None)
        assert getattr(exc_info.value, "code", None) == CODE_TASK_NOT_FOUND

    def test_stream_video_non_video_task_raises(self, engine):
        service = PreviewService(engine=engine, transport=httpx.MockTransport(handler=None))
        _seed_media_task(
            engine,
            task_id=IMAGE_TASK_ID,
            media_type=MediaType.IMAGE,
            metadata={"images": [{"url": "https://cdn.example.com/1.jpg"}]},
        )
        with pytest.raises(Exception) as exc_info:
            service.stream_video(IMAGE_TASK_ID, range_header=None)
        assert getattr(exc_info.value, "code", None) == CODE_BAD_REQUEST

    def test_stream_video_missing_media_url_raises(self, engine):
        service = PreviewService(engine=engine, transport=httpx.MockTransport(handler=None))
        _seed_media_task(engine, task_id=VIDEO_TASK_ID, metadata={})
        with pytest.raises(Exception) as exc_info:
            service.stream_video(VIDEO_TASK_ID, range_header=None)
        assert getattr(exc_info.value, "code", None) == CODE_BAD_REQUEST

    def test_stream_video_upstream_transport_error_raises_400(self, engine):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("boom", request=request)

        service = PreviewService(engine=engine, transport=httpx.MockTransport(handler))
        _seed_media_task(
            engine,
            task_id=VIDEO_TASK_ID,
            metadata={"video_url": "https://cdn.example.com/v.mp4"},
        )
        with pytest.raises(Exception) as exc_info:
            service.stream_video(VIDEO_TASK_ID, range_header=None)
        assert getattr(exc_info.value, "code", None) == CODE_BAD_REQUEST

    def test_stream_video_rejects_hls_case_variants(self, engine):
        # Content-Type case variant: application/x-mpegURL.
        def handler_mpegurl(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/x-mpegURL"},
                content=b"#EXTM3U\n",
            )

        service = PreviewService(engine=engine, transport=httpx.MockTransport(handler_mpegurl))
        _seed_media_task(
            engine,
            task_id=VIDEO_TASK_ID,
            metadata={"video_url": "https://cdn.example.com/playlist.m3u8"},
        )
        with pytest.raises(Exception) as exc_info:
            service.stream_video(VIDEO_TASK_ID, range_header=None)
        assert getattr(exc_info.value, "code", None) == CODE_BAD_REQUEST

        # URL extension case variant: playlist.M3U8 with an opaque content type.
        def handler_octet(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/octet-stream"},
                content=b"#EXTM3U\n",
            )

        service = PreviewService(engine=engine, transport=httpx.MockTransport(handler_octet))
        task_id = make_task_id()
        _seed_media_task(
            engine,
            task_id=task_id,
            metadata={"video_url": "https://cdn.example.com/playlist.M3U8"},
        )
        with pytest.raises(Exception) as exc_info:
            service.stream_video(task_id, range_header=None)
        assert getattr(exc_info.value, "code", None) == CODE_BAD_REQUEST

    def test_stream_video_http_error_suppresses_close_failure(self, engine, caplog):
        close_calls = []

        def close():
            close_calls.append(True)
            raise RuntimeError("bug https://cdn.example/private-token")

        class BrokenUpstream:
            def stream(self, url, **kwargs):
                return UpstreamStream(
                    status_code=404,
                    content_type="video/mp4",
                    headers={},
                    chunks=iter(()),
                    close=close,
                )

        _seed_media_task(
            engine,
            task_id=VIDEO_TASK_ID,
            metadata={"video_url": "https://cdn.example.com/v.mp4"},
        )
        service = PreviewService(engine=engine, upstream=BrokenUpstream())

        with caplog.at_level(logging.ERROR), pytest.raises(ApiError) as exc_info:
            service.stream_video(VIDEO_TASK_ID, range_header=None)

        assert exc_info.value.code == CODE_BAD_REQUEST
        assert close_calls == [True]
        assert "cdn.example" not in caplog.text
        assert "private-token" not in caplog.text

    def test_stream_video_hls_suppresses_close_failure(self, engine, caplog):
        close_calls = []

        def close():
            close_calls.append(True)
            raise RuntimeError("bug https://cdn.example/private-token")

        class BrokenUpstream:
            def stream(self, url, **kwargs):
                return UpstreamStream(
                    status_code=200,
                    content_type="application/vnd.apple.mpegurl",
                    headers={},
                    chunks=iter(()),
                    close=close,
                )

        _seed_media_task(
            engine,
            task_id=VIDEO_TASK_ID,
            metadata={"video_url": "https://cdn.example.com/playlist.m3u8"},
        )
        service = PreviewService(engine=engine, upstream=BrokenUpstream())

        with caplog.at_level(logging.ERROR), pytest.raises(ApiError) as exc_info:
            service.stream_video(VIDEO_TASK_ID, range_header=None)

        assert exc_info.value.code == CODE_BAD_REQUEST
        assert close_calls == [True]
        assert "cdn.example" not in caplog.text
        assert "private-token" not in caplog.text

    def test_stream_resource_http_error_suppresses_close_failure(self, engine, caplog):
        close_calls = []

        def close():
            close_calls.append(True)
            raise RuntimeError("bug https://cdn.example/private-token")

        class BrokenUpstream:
            def stream(self, url, **kwargs):
                return UpstreamStream(
                    status_code=404,
                    content_type="image/jpeg",
                    headers={},
                    chunks=iter(()),
                    close=close,
                )

        task_id = make_task_id()
        _seed_media_task(
            engine,
            task_id=task_id,
            media_type=MediaType.IMAGE,
            metadata={
                "manifest": {
                    "kind": "image_album",
                    "images": [
                        {"url": "https://cdn.example/a.jpg", "format": "jpg"}
                    ],
                }
            },
        )
        service = PreviewService(engine=engine, upstream=BrokenUpstream())

        with caplog.at_level(logging.ERROR), pytest.raises(ApiError) as exc_info:
            service.stream_resource(task_id, "image", 0)

        assert exc_info.value.code == CODE_BAD_REQUEST
        assert close_calls == [True]
        assert "cdn.example" not in caplog.text
        assert "private-token" not in caplog.text


class TestImageDownload:
    def _image_handler(self):
        def handler(request: httpx.Request) -> httpx.Response:
            name = request.url.path.rsplit("/", 1)[-1]
            ext = name.rsplit(".", 1)[-1] if "." in name else "jpg"
            if ext == "png":
                body = b"\x89PNG-fake"
            else:
                body = b"JPEG-fake"
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=body)
        return handler

    def test_image_bytes_returns_single_image(self, engine):
        service = PreviewService(
            engine=engine, transport=httpx.MockTransport(self._image_handler())
        )
        _seed_media_task(
            engine,
            task_id=IMAGE_TASK_ID,
            media_type=MediaType.IMAGE,
            metadata={
                "images": [
                    {"url": "https://cdn.example.com/a.jpg"},
                    {"url": "https://cdn.example.com/b.png"},
                ]
            },
        )
        body, content_type, filename = service.image_bytes(IMAGE_TASK_ID, 1)
        assert body == b"\x89PNG-fake"
        assert filename == "image-0002.png"

    def test_image_bytes_index_out_of_range_raises(self, engine):
        service = PreviewService(engine=engine, transport=httpx.MockTransport(handler=None))
        _seed_media_task(
            engine,
            task_id=IMAGE_TASK_ID,
            media_type=MediaType.IMAGE,
            metadata={"images": [{"url": "https://cdn.example.com/a.jpg"}]},
        )
        with pytest.raises(Exception) as exc_info:
            service.image_bytes(IMAGE_TASK_ID, 5)
        assert getattr(exc_info.value, "code", None) == CODE_BAD_REQUEST

    def test_album_zip_bundles_all_images(self, engine):
        service = PreviewService(
            engine=engine, transport=httpx.MockTransport(self._image_handler())
        )
        _seed_media_task(
            engine,
            task_id=IMAGE_TASK_ID,
            media_type=MediaType.IMAGE,
            title="度假相册",
            metadata={
                "images": [
                    {"url": "https://cdn.example.com/a.jpg"},
                    {"url": "https://cdn.example.com/b.png"},
                ]
            },
        )
        body, filename = service.album_zip(IMAGE_TASK_ID)
        assert filename == "du-jia-xiang-ce-2-images.zip" or filename.endswith("-2-images.zip")
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            names = archive.namelist()
            assert names == ["image-0001.jpg", "image-0002.png"]
            assert archive.read("image-0001.jpg") == b"JPEG-fake"
            assert archive.read("image-0002.png") == b"\x89PNG-fake"

    def test_album_zip_empty_album_raises(self, engine):
        service = PreviewService(engine=engine, transport=httpx.MockTransport(handler=None))
        _seed_media_task(
            engine,
            task_id=IMAGE_TASK_ID,
            media_type=MediaType.IMAGE,
            metadata={},
        )
        with pytest.raises(Exception) as exc_info:
            service.album_zip(IMAGE_TASK_ID)
        assert getattr(exc_info.value, "code", None) == CODE_BAD_REQUEST
