"""Tests for the f2 parser adapter (Tasks 6-9): routing, per-platform mapping,
cookie classification, and f2 error translation. All f2 calls are faked by
monkeypatching the module-level names the adapter imports — the suite never
touches the network."""

import asyncio
import re
from unittest.mock import Mock

import httpx
import pytest

import app.adapters.parser_f2 as parser_f2
from app.adapters.engine_errors import (
    CookieInvalidError,
    CookieMissingError,
    EngineNetworkError,
    EngineParseError,
    EngineTimeoutError,
    PlatformBlockedError,
    UnsupportedPlatformError,
)
from app.adapters.parser_f2 import F2ParserAdapter
from app.domain import MediaType, ParseCommand

_URL_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class FakeCookieProvider:
    def __init__(self, cookies: dict[str, str | None]):
        self._cookies = cookies

    def get(self, platform: str) -> str | None:
        return self._cookies.get(platform)


def _async_returns(value):
    """A fake async function that resolves to ``value``.

    The adapter awaits every f2 call (``await AwemeIdFetcher.get_aweme_id(...)``,
    ``await ...handler.fetch_one_video(...)``), so plain ``Mock(return_value=x)``
    would raise ``TypeError: object ... can't be used in 'await' expression``.
    """

    async def inner(*args, **kwargs):
        return value

    return inner


def _stub_f2(monkeypatch, mapping):
    """Stub the adapter's lazy f2 imports with fakes.

    ``mapping`` maps ``(module_name, attr_name)`` → the fake value
    :meth:`F2ParserAdapter._import_f2` should return. This keeps the suite
    free of real f2 imports, whose module-import time hits external token
    APIs (see the adapter docstring).
    """

    def fake_import(module_name, attr_name):
        return mapping[(module_name, attr_name)]

    monkeypatch.setattr(F2ParserAdapter, "_import_f2", fake_import)


def _offline_adapter(**kwargs) -> F2ParserAdapter:
    """An adapter whose size probe never touches the network (no length)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={}, content=b"")

    return F2ParserAdapter(transport=httpx.MockTransport(handler), **kwargs)


class TestRouting:
    def test_douyin_short_and_full_hosts_route(self):
        for url in (
            "https://v.douyin.com/abc/",
            "https://www.douyin.com/video/123",
            "https://www.iesdouyin.com/share/video/123",
        ):
            assert parser_f2._route(url) == "douyin"

    def test_weibo_hosts_route(self):
        assert parser_f2._route("https://weibo.com/123/AbC") == "weibo"
        assert parser_f2._route("https://m.weibo.cn/detail/456") == "weibo"

    def test_tiktok_hosts_route(self):
        assert parser_f2._route("https://www.tiktok.com/@u/video/123") == "tiktok"
        assert parser_f2._route("https://vm.tiktok.com/abc/") == "tiktok"

    def test_unknown_and_false_positive_hosts_do_not_route(self):
        for url in (
            "https://example.com/x",
            "https://weibo.com.evil.example/x",
            "https://best.co/track/1",
            "https://bilibili.com/video/BV1xx",
            "https://www.xiaohongshu.com/explore/1",
        ):
            assert parser_f2._route(url) is None


class TestErrorTranslation:
    def test_unauthorized_becomes_cookie_invalid(self):
        from f2.exceptions import APIUnauthorizedError

        error = parser_f2._translate_f2_error(
            APIUnauthorizedError("rejected"), url="https://v.douyin.com/abc/"
        )
        assert isinstance(error, CookieInvalidError)

    def test_timeout_becomes_engine_timeout(self):
        from f2.exceptions import APITimeoutError

        error = parser_f2._translate_f2_error(
            APITimeoutError("slow"), url="https://v.douyin.com/abc/"
        )
        assert isinstance(error, EngineTimeoutError)

    def test_connection_becomes_engine_network(self):
        from f2.exceptions import APIConnectionError

        error = parser_f2._translate_f2_error(
            APIConnectionError("refused"), url="https://v.douyin.com/abc/"
        )
        assert isinstance(error, EngineNetworkError)

    def test_rate_limit_becomes_platform_blocked(self):
        from f2.exceptions import APIRateLimitError

        error = parser_f2._translate_f2_error(
            APIRateLimitError("too fast"), url="https://v.douyin.com/abc/"
        )
        assert isinstance(error, PlatformBlockedError)

    def test_generic_api_error_becomes_engine_parse_with_class_name(self):
        from f2.exceptions import APIResponseError

        error = parser_f2._translate_f2_error(
            APIResponseError("empty"), url="https://v.douyin.com/abc/"
        )
        assert isinstance(error, EngineParseError)
        assert "APIResponseError" in str(error)

    def test_unknown_exception_falls_back_to_generic_translation(self):
        error = parser_f2._translate_f2_error(
            ValueError("boom"), url="https://v.douyin.com/abc/"
        )
        assert isinstance(error, EngineParseError)

    def test_asyncio_timeout_from_wait_for_translates(self):
        # The sync wrapper surfaces asyncio.TimeoutError; translation must
        # classify it as a parse timeout.
        error = parser_f2._translate_f2_error(
            asyncio.TimeoutError(), url="https://v.douyin.com/abc/"
        )
        assert isinstance(error, EngineTimeoutError)


class TestBuilder:
    def test_video_result_shape(self):
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({}))
        result = adapter._build_result(
            url="https://v.douyin.com/abc/",
            platform="douyin",
            title="示例",
            cover="https://cdn.example/c.jpg",
            duration_ms=83000,
            video_url="https://cdn.example/v.mp4",
            images=[],
            author={"uid": "1", "name": "张三", "avatar": None},
        )
        assert result.media_type is MediaType.VIDEO
        assert result.format == "mp4"
        assert result.duration == "01:23"  # 83 s -> MM:SS
        assert result.platform == "douyin"
        assert result.metadata["engine"] == "f2"
        assert result.metadata["video_url"] == "https://cdn.example/v.mp4"
        assert _URL_RE.fullmatch(result.task_id) is not None

    def test_image_album_result_shape(self):
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({}))
        result = adapter._build_result(
            url="https://v.douyin.com/note/1",
            platform="douyin",
            title="图集",
            cover=None,
            duration_ms=None,
            video_url=None,
            images=["https://cdn.example/a.jpg", "https://cdn.example/b.webp"],
            author={"uid": "1", "name": "张三", "avatar": None},
        )
        assert result.media_type is MediaType.IMAGE
        assert result.format == "jpg"  # first image's extension
        assert len(result.metadata["images"]) == 2
        assert result.metadata["images"][0]["url"] == "https://cdn.example/a.jpg"

    def test_no_media_raises_engine_parse_error(self):
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({}))
        with pytest.raises(EngineParseError):
            adapter._build_result(
                url="https://v.douyin.com/x",
                platform="douyin",
                title="x",
                cover=None,
                duration_ms=None,
                video_url=None,
                images=[],
                author={"uid": "1", "name": "n", "avatar": None},
            )

    def test_blank_title_and_cover_are_normalized(self):
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({}))
        result = adapter._build_result(
            url="https://v.douyin.com/abc/",
            platform="douyin",
            title="  ",
            cover="",
            duration_ms=83000,
            video_url="https://cdn.example/v.mp4",
            images=[],
            author={"uid": "1", "name": "张三", "avatar": None},
        )
        assert result.title == "douyin"  # blank title falls back to platform
        assert result.cover is None

    def test_zero_and_float_durations(self):
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({}))
        zero = adapter._build_result(
            url="https://v.douyin.com/a",
            platform="douyin",
            title="x",
            cover=None,
            duration_ms=0,
            video_url="https://cdn.example/v.mp4",
            images=[],
            author={"uid": "1", "name": "n", "avatar": None},
        )
        assert zero.duration is None  # 0 ms = unknown, not "00:00"

        floored = adapter._build_result(
            url="https://v.douyin.com/b",
            platform="douyin",
            title="x",
            cover=None,
            duration_ms=83000.5,
            video_url="https://cdn.example/v.mp4",
            images=[],
            author={"uid": "1", "name": "n", "avatar": None},
        )
        assert floored.duration == "01:23"

    def test_non_string_images_are_dropped(self):
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({}))
        result = adapter._build_result(
            url="https://v.douyin.com/note/1",
            platform="douyin",
            title="图集",
            cover=None,
            duration_ms=None,
            video_url=None,
            images=["https://cdn.example/a.jpg", None, 123],
            author={"uid": "1", "name": "张三", "avatar": None},
        )
        assert result.media_type is MediaType.IMAGE
        assert [img["url"] for img in result.metadata["images"]] == [
            "https://cdn.example/a.jpg"
        ]

    def test_unsupported_url_raises_unsupported_platform(self):
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({}))
        with pytest.raises(UnsupportedPlatformError):
            adapter.parse(ParseCommand(urls=["https://www.bilibili.com/video/BV1xx"]))
