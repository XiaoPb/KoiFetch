"""Tests for the engine parser adapter (Task 5): platform routing and the
parse-video-py integration. All engine calls are faked (monkeypatched
parse_video_share_url, httpx.MockTransport for the size probe) — the suite
never touches the network."""

import asyncio
import re

import httpx
import pytest

import app.adapters.parser_engine as parser_engine
from app.adapters.engine_errors import (
    EngineNetworkError,
    EngineParseError,
    EngineTimeoutError,
    PlatformBlockedError,
    UnsupportedPlatformError,
)
from app.adapters.parser_engine import EngineParserAdapter
from app.domain import MediaType, ParseCommand
from parse_video_py import ImgInfo, VideoAuthor, VideoInfo

_URL_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _fake_video_info(**overrides) -> VideoInfo:
    base = dict(
        video_url="https://cdn.example/v.mp4",
        cover_url="https://cdn.example/c.jpg",
        title="晴天示例",
        music_url="https://cdn.example/m.mp3",
        images=[],
        author=VideoAuthor(uid="1", name="张三", avatar="https://cdn.example/a.jpg"),
    )
    base.update(overrides)
    return VideoInfo(**base)


def _offline_adapter() -> EngineParserAdapter:
    """An adapter whose size probe never touches the network (no length)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={}, content=b"")

    return EngineParserAdapter(transport=httpx.MockTransport(handler))


class TestRouting:
    def test_video_platform_routes_to_parse_video_py(self, monkeypatch):
        calls = []

        async def fake_parse(url):
            calls.append(url)
            return _fake_video_info()

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        adapter = _offline_adapter()
        results = adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))
        assert len(results) == 1
        assert calls == ["https://v.douyin.com/abc/"]

    def test_music_platform_raises_unsupported(self):
        adapter = EngineParserAdapter()
        with pytest.raises(UnsupportedPlatformError):
            adapter.parse(ParseCommand(urls=["https://music.163.com/#/song?id=1"]))
        with pytest.raises(UnsupportedPlatformError):
            adapter.parse(ParseCommand(urls=["https://y.qq.com/n/ryqq/songDetail/001x"]))

    def test_unknown_platform_raises_unsupported(self):
        adapter = EngineParserAdapter()
        with pytest.raises(UnsupportedPlatformError):
            adapter.parse(ParseCommand(urls=["https://example.com/things/xyz"]))

    def test_quanminkge_stays_video_not_music(self, monkeypatch):
        # kg.qq.com is 全民K歌 (video) in parse-video-py, not QQ music.
        async def fake_parse(url):
            return _fake_video_info()

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        result = _offline_adapter().parse(
            ParseCommand(urls=["https://kg.qq.com/node/play?s=abc"])
        )[0]
        assert result.platform == "quanminkge"
        assert result.media_type is MediaType.VIDEO

    def test_route_table_covers_engine_mapping(self):
        # Drift guard: every domain_list entry the installed parse-video-py
        # knows must be routable by our adapter.
        from parse_video_py.parser import video_source_info_mapping
        video_hosts = {host for host, _ in parser_engine._VIDEO_ROUTES}
        for info in video_source_info_mapping.values():
            for domain in info["domain_list"]:
                assert any(domain in video_host for video_host in video_hosts), domain

    def test_hostname_false_positives_rejected(self):
        # Substring matching would route these wrongly (box.com contains
        # "x.com", 36.cn contains "6.cn", tv.sohu.com.evil contains "sohu.com").
        adapter = EngineParserAdapter()
        for url in (
            "https://box.com/video/1",
            "https://best.co/track/1",
            "https://36.cn/clip/1",
            "https://tv.sohu.com.evil.example/video/1",
            "https://weibo.com.evil/video/1",
        ):
            with pytest.raises(UnsupportedPlatformError):
                adapter.parse(ParseCommand(urls=[url]))

    def test_subdomains_still_route(self, monkeypatch):
        async def fake_parse(url):
            return _fake_video_info()

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        for url in (
            "https://v.douyin.com/abc/",
            "https://www.bilibili.com/video/BV1xx",
            "https://m.bilibili.com/video/BV1xx",
            "https://b23.tv/abc",
            "https://www.xiaohongshu.com/explore/1",
            "https://xhslink.com/abc",
        ):
            result = _offline_adapter().parse(ParseCommand(urls=[url]))[0]
            assert result.media_type is MediaType.VIDEO


class TestVideoMapping:
    def test_parse_maps_video_info_to_parse_result(self, monkeypatch):
        async def fake_parse(url):
            return _fake_video_info()

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        adapter = _offline_adapter()
        result = adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))[0]
        assert result.title == "晴天示例"
        assert result.cover == "https://cdn.example/c.jpg"
        assert result.platform == "douyin"
        assert result.media_type is MediaType.VIDEO
        assert result.format == "mp4"
        assert result.duration is None  # engine exposes no duration (documented)
        assert result.available_qualities == []
        assert result.available_bitrates == []
        assert result.metadata["engine"] == "parse-video-py"
        assert result.metadata["video_url"] == "https://cdn.example/v.mp4"
        assert result.metadata["music_url"] == "https://cdn.example/m.mp3"
        assert result.metadata["author"]["name"] == "张三"
        assert _URL_RE.fullmatch(result.task_id) is not None

    def test_empty_title_falls_back_to_platform(self, monkeypatch):
        async def fake_parse(url):
            return _fake_video_info(title="")

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        result = _offline_adapter().parse(
            ParseCommand(urls=["https://v.douyin.com/abc/"])
        )[0]
        assert result.title == "douyin"

    def test_task_id_is_fresh_per_parse(self, monkeypatch):
        async def fake_parse(url):
            return _fake_video_info()

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        adapter = _offline_adapter()
        first = adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))[0]
        second = adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))[0]
        assert first.task_id != second.task_id

    def test_image_album_classified_as_image(self, monkeypatch):
        # 图集/动图: the engine returns the album in `images` with no
        # video_url — the result must be IMAGE with a real image format.
        async def fake_parse(url):
            return _fake_video_info(
                video_url="",
                cover_url="https://cdn.example/cover.jpg",
                images=[
                    ImgInfo(url="https://cdn.example/a.gif", live_photo_url=""),
                    ImgInfo(url="https://cdn.example/b.jpg", live_photo_url=""),
                ],
            )

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        result = _offline_adapter().parse(
            ParseCommand(urls=["https://v.douyin.com/abc/"])
        )[0]
        assert result.media_type is MediaType.IMAGE
        assert result.format == "gif"  # 动图: animated image stays an image
        assert result.cover == "https://cdn.example/cover.jpg"
        assert len(result.metadata["images"]) == 2
        assert result.metadata["images"][0]["url"] == "https://cdn.example/a.gif"

    def test_video_with_images_stays_video(self, monkeypatch):
        # Some platforms return BOTH a video_url and thumbnails — that is a
        # video, not an album.
        async def fake_parse(url):
            return _fake_video_info(images=[ImgInfo(url="https://cdn.example/t.jpg", live_photo_url="")])

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        result = _offline_adapter().parse(
            ParseCommand(urls=["https://v.douyin.com/abc/"])
        )[0]
        assert result.media_type is MediaType.VIDEO
        assert result.format == "mp4"


class TestFileSizeProbe:
    def _adapter_with_probe(self, content_length: str | None):
        def handler(request: httpx.Request) -> httpx.Response:
            headers = {}
            if content_length is not None:
                headers["content-length"] = content_length
            return httpx.Response(200, headers=headers, content=b"")

        return EngineParserAdapter(transport=httpx.MockTransport(handler))

    def test_file_size_from_content_length(self, monkeypatch):
        async def fake_parse(url):
            return _fake_video_info()

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        result = self._adapter_with_probe("1048576").parse(
            ParseCommand(urls=["https://v.douyin.com/abc/"])
        )[0]
        assert result.file_size_mb == pytest.approx(1.0)

    def test_probe_failure_leaves_size_none(self, monkeypatch):
        async def fake_parse(url):
            return _fake_video_info()

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        adapter = EngineParserAdapter(transport=httpx.MockTransport(handler))
        result = adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))[0]
        assert result.file_size_mb is None
        assert result.error is None  # the probe is best-effort, never fatal


class TestEngineErrorTranslation:
    def test_connect_error_becomes_engine_network_error(self, monkeypatch):
        async def fake_parse(url):
            raise httpx.ConnectError("refused", request=httpx.Request("GET", url))

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        with pytest.raises(EngineNetworkError):
            EngineParserAdapter().parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))

    def test_timeout_becomes_engine_timeout_error(self, monkeypatch):
        async def fake_parse(url):
            raise httpx.ReadTimeout("slow", request=httpx.Request("GET", url))

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        with pytest.raises(EngineTimeoutError):
            EngineParserAdapter().parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))

    def test_asyncio_timeout_becomes_engine_timeout_error(self, monkeypatch):
        async def fake_parse(url):
            raise asyncio.TimeoutError

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        with pytest.raises(EngineTimeoutError):
            EngineParserAdapter().parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))

    def test_403_becomes_platform_blocked_error(self, monkeypatch):
        async def fake_parse(url):
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError(
                "Forbidden", request=request, response=httpx.Response(403, request=request)
            )

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        with pytest.raises(PlatformBlockedError):
            EngineParserAdapter().parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))

    def test_value_error_becomes_engine_parse_error(self, monkeypatch):
        async def fake_parse(url):
            raise ValueError("parse video json info from html fail")

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        with pytest.raises(EngineParseError):
            EngineParserAdapter().parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))

    def test_slow_engine_call_hits_configured_timeout(self, monkeypatch):
        async def slow_parse(url):
            await asyncio.sleep(5)

        monkeypatch.setattr(parser_engine, "parse_video_share_url", slow_parse)
        with pytest.raises(EngineTimeoutError):
            EngineParserAdapter(timeout_seconds=0.01).parse(
                ParseCommand(urls=["https://v.douyin.com/abc/"])
            )

    def test_construction_failure_becomes_engine_parse_error(self, monkeypatch):
        # Odd engine data (a field access raising) must translate into a typed
        # EngineParseError, not leak a raw AttributeError.
        class BrokenInfo:
            @property
            def title(self):
                raise AttributeError("missing title")

        async def fake_parse(url):
            return BrokenInfo()

        monkeypatch.setattr(parser_engine, "parse_video_share_url", fake_parse)
        with pytest.raises(EngineParseError):
            _offline_adapter().parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))
