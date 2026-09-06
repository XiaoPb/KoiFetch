"""Tests for the f2 parser adapter (Tasks 6-9): routing, per-platform mapping,
cookie classification, and f2 error translation. All f2 calls are faked by
monkeypatching the module-level names the adapter imports — the suite never
touches the network."""

import asyncio
import logging
import re
from types import SimpleNamespace
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
from app.domain import MediaResource, MediaType, ParseCommand

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

    def fake_import(_self, module_name, attr_name):
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


def test_build_result_logs_all_resolved_media_link_groups(caplog):
    caplog.set_level(logging.INFO, logger="app.adapters.parser_f2")
    adapter = _offline_adapter()
    result = adapter._build_result(
        url="https://example.com/post/1",
        platform="douyin",
        title="album",
        cover=None,
        duration_ms=None,
        images=["https://cdn.example/image-1.jpg", "https://cdn.example/image-2.jpg"],
        author={"uid": "u1", "name": "author", "avatar": None},
        extra_metadata={"background_music_urls": ["https://cdn.example/music.mp3"]},
    )

    assert result.metadata["manifest"]["kind"] == "image_album"
    message = " ".join(record.getMessage() for record in caplog.records)
    assert "image-1.jpg" in message
    assert "image-2.jpg" in message
    assert "music.mp3" in message


class TestHandlerKwargs:
    def test_extracts_uifid_and_sets_argus_header(self):
        adapter = F2ParserAdapter(proxy="http://127.0.0.1:7890")

        kwargs = adapter._kwargs(
            "douyin",
            "sid_guard=abc; UIFID=uifid-value; sessionid=session-value",
        )

        assert kwargs["headers"]["uifid"] == "uifid-value"
        assert kwargs["headers"]["x-tt-argus"] == "1"
        assert kwargs["cookie"].startswith("sid_guard=abc;")
        assert kwargs["proxies"] == {
            "http://": "http://127.0.0.1:7890",
            "https://": "http://127.0.0.1:7890",
        }


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


def _fake_post_detail(**overrides):
    """A faked douyin PostDetailFilter (duck-typed; tests only read attrs)."""
    defaults = dict(
        api_status_code=0,
        nickname="张三",
        uid="12345",
        aweme_id="aweme-1",
        create_time="2026-09-06T12:00:00+08:00",
        desc="示例视频",
        cover="https://cdn.example/c.jpg",
        duration=83000,
        video_play_addr=["https://cdn.example/v.mp4"],
        images=[],
        images_video=[],
        aweme_type=0,
        music_play_url=None,
    )
    defaults.update(overrides)
    return type("FakePostDetail", (), defaults)()


def _fake_weibo_detail(**overrides):
    """A faked weibo WeiboDetailFilter (duck-typed).

    The real filter exposes the pic dict as the public ``weibo_pic_infos``
    property; the fake mirrors that shape.
    """
    defaults = dict(
        error_code=0,
        weibo_desc="示例微博",
        desc="<p>示例微博</p>",
        nickname="博主",
        uid="u1",
        post_id="post-1",
        created_at="2026-09-05T08:00:00Z",
        playback_list=["https://cdn.example/w.mp4"],
        weibo_pic_infos={},
    )
    defaults.update(overrides)
    return type("FakeWeiboDetail", (), defaults)()


class TestDouyinMapping:
    def _adapter(self, **kwargs):
        return _offline_adapter(cookie_provider=FakeCookieProvider({"douyin": "d=1"}), **kwargs)

    def test_maps_video_detail(self, monkeypatch):
        fake_handler = Mock(
            fetch_one_video=_async_returns(
                _fake_post_detail(
                    music_play_url="https://cdn.example/music.mp3?token=secret"
                )
            )
        )
        handler_cls = Mock(return_value=fake_handler)
        _stub_f2(monkeypatch, {
            ("f2.apps.douyin.utils", "AwemeIdFetcher"): Mock(get_aweme_id=_async_returns("1")),
            ("f2.apps.douyin.handler", "DouyinHandler"): handler_cls,
        })

        result = self._adapter().parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))[0]
        assert result.platform == "douyin"
        assert result.media_type is MediaType.VIDEO
        assert result.title == "示例视频"
        assert result.cover == "https://cdn.example/c.jpg"
        assert result.duration == "01:23"
        assert result.metadata["video_url"] == "https://cdn.example/v.mp4"
        assert result.metadata["background_music_urls"] == [
            "https://cdn.example/music.mp3?token=secret"
        ]
        assert result.metadata["author"]["name"] == "张三"
        assert result.metadata["source_id"] == "1"
        assert result.metadata["published_at"] == "2026-09-06"
        # The cookie must be forwarded into the handler kwargs (first positional
        # argument of the DouyinHandler constructor).
        assert handler_cls.call_args[0][0]["cookie"] == "d=1"

    def test_note_album_classified_as_image(self, monkeypatch):
        fake_handler = Mock(
            fetch_one_video=_async_returns(
                _fake_post_detail(
                    desc="图集",
                    cover=None,
                    duration=None,
                    video_play_addr=[],
                    images=["https://cdn.example/a.jpg", "https://cdn.example/b.webp"],
                )
            )
        )
        _stub_f2(monkeypatch, {
            ("f2.apps.douyin.utils", "AwemeIdFetcher"): Mock(get_aweme_id=_async_returns("2")),
            ("f2.apps.douyin.handler", "DouyinHandler"): Mock(return_value=fake_handler),
        })

        result = self._adapter().parse(ParseCommand(urls=["https://www.douyin.com/note/2"]))[0]
        assert result.media_type is MediaType.IMAGE
        assert result.format == "jpg"
        assert len(result.metadata["images"]) == 2

    def test_empty_title_falls_back_to_platform(self, monkeypatch):
        fake_handler = Mock(fetch_one_video=_async_returns(_fake_post_detail(desc="")))
        _stub_f2(monkeypatch, {
            ("f2.apps.douyin.utils", "AwemeIdFetcher"): Mock(get_aweme_id=_async_returns("3")),
            ("f2.apps.douyin.handler", "DouyinHandler"): Mock(return_value=fake_handler),
        })
        result = self._adapter().parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))[0]
        assert result.title == "douyin"

    def test_video_wins_when_images_also_present(self, monkeypatch):
        fake_handler = Mock(
            fetch_one_video=_async_returns(
                _fake_post_detail(images=["https://cdn.example/a.jpg"])
            )
        )
        _stub_f2(monkeypatch, {
            ("f2.apps.douyin.utils", "AwemeIdFetcher"): Mock(get_aweme_id=_async_returns("4")),
            ("f2.apps.douyin.handler", "DouyinHandler"): Mock(return_value=fake_handler),
        })
        result = self._adapter().parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))[0]
        assert result.media_type is MediaType.VIDEO

    def test_falsy_image_entries_are_filtered(self, monkeypatch):
        fake_handler = Mock(
            fetch_one_video=_async_returns(
                _fake_post_detail(
                    desc="图集",
                    video_play_addr=[],
                    images=["https://cdn.example/a.jpg", None, ""],
                )
            )
        )
        _stub_f2(monkeypatch, {
            ("f2.apps.douyin.utils", "AwemeIdFetcher"): Mock(get_aweme_id=_async_returns("5")),
            ("f2.apps.douyin.handler", "DouyinHandler"): Mock(return_value=fake_handler),
        })
        result = self._adapter().parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))[0]
        assert result.media_type is MediaType.IMAGE
        assert [img["url"] for img in result.metadata["images"]] == [
            "https://cdn.example/a.jpg"
        ]

    def test_aweme_68_maps_ordered_live_photo_pairs(self):
        data = _fake_post_detail(
            aweme_type=68,
            video_play_addr=[],
            images=["https://cdn.example/a.webp", "https://cdn.example/b.webp"],
            images_video=["https://cdn.example/a.mp4", "https://cdn.example/b.mp4"],
        )

        result = self._adapter()._map_douyin(data, "https://douyin.com/video/1")

        assert result.media_type is MediaType.LIVE_PHOTO
        assert result.metadata["manifest"]["kind"] == "live_photo"
        assert result.metadata["manifest"]["live_photos"][0]["image"]["url"].endswith(
            "a.webp"
        )
        assert result.metadata["manifest"]["live_photos"][1]["motion"][
            "url"
        ].endswith("b.mp4")

    def test_unpaired_image_is_kept_with_warning(self):
        data = _fake_post_detail(
            aweme_type=68,
            video_play_addr=[],
            images=["https://cdn.example/a.webp", "https://cdn.example/b.webp"],
            images_video=["https://cdn.example/a.mp4"],
        )

        result = self._adapter()._map_douyin(data, "https://douyin.com/video/1")

        pairs = result.metadata["manifest"]["live_photos"]
        assert pairs[1]["motion"] is None
        assert result.metadata["manifest"]["warnings"] == [
            "1 张实况照片缺少动态视频"
        ]

    def test_non_live_video_stays_video(self):
        data = _fake_post_detail(
            aweme_type=0,
            images=[],
            images_video=[],
            video_play_addr=["https://cdn.example/video.mp4"],
        )

        assert self._adapter()._map_douyin(
            data, "https://douyin.com/video/1"
        ).media_type is MediaType.VIDEO

    def test_images_video_without_images_falls_back_to_video_or_errors(self):
        data = _fake_post_detail(
            aweme_type=0,
            images=[],
            images_video=["https://cdn.example/orphan.mp4"],
            video_play_addr=[],
        )

        with pytest.raises(EngineParseError):
            self._adapter()._map_douyin(data, "https://douyin.com/video/1")

    def test_extra_motion_is_ignored_with_deterministic_warning(self):
        data = _fake_post_detail(
            aweme_type=68,
            video_play_addr=[],
            images=["https://cdn.example/a.webp"],
            images_video=[
                "https://cdn.example/a.mp4",
                "https://cdn.example/orphan.mp4",
            ],
        )

        result = self._adapter()._map_douyin(data, "https://douyin.com/video/1")

        manifest = result.metadata["manifest"]
        assert len(manifest["live_photos"]) == 1
        assert manifest["warnings"] == ["1 个动态视频没有对应静态图片"]

    def test_live_photo_legacy_fields_and_resource_extensions_are_derived(self):
        data = _fake_post_detail(
            aweme_type=68,
            video_play_addr=[],
            cover=None,
            images=[" https://cdn.example/A.WEBP?token=1 "],
            images_video=["https://cdn.example/A.MP4?token=2"],
        )

        result = self._adapter()._map_douyin(data, "https://douyin.com/video/1")

        manifest = result.metadata["manifest"]
        assert result.cover == "https://cdn.example/A.WEBP?token=1"
        assert result.format == "webp"
        assert manifest["live_photos"][0]["image"]["format"] == "webp"
        assert manifest["live_photos"][0]["motion"]["format"] == "mp4"
        assert result.metadata["video_url"] is None
        assert result.metadata["images"] == [
            {
                "url": "https://cdn.example/A.WEBP?token=1",
                "live_photo_url": "https://cdn.example/A.MP4?token=2",
            }
        ]

    def test_scalar_and_url_bearing_objects_are_extracted_in_order(self):
        data = _fake_post_detail(
            aweme_type=68,
            video_play_addr=[],
            images=(
                {"url": "https://cdn.example/a.webp"},
                SimpleNamespace(url="https://cdn.example/b.webp"),
                MediaResource(url="https://cdn.example/c.webp", format="webp"),
                "https://cdn.example/b.webp",
            ),
            images_video="https://cdn.example/a.mp4",
        )

        result = self._adapter()._map_douyin(data, "https://douyin.com/video/1")

        assert [
            pair["image"]["url"] for pair in result.metadata["manifest"]["live_photos"]
        ] == [
            "https://cdn.example/a.webp",
            "https://cdn.example/b.webp",
            "https://cdn.example/c.webp",
        ]

    def test_invalid_url_items_are_skipped_without_poisoning_valid_items(self):
        class BrokenUrl:
            @property
            def url(self):
                raise RuntimeError("broken URL property")

        data = _fake_post_detail(
            aweme_type=68,
            video_play_addr=[],
            images=[
                "https://cdn.example/a.webp",
                "http://[broken-ipv6",
                "http://cdn.example:bad-port/b.webp",
                "ftp://cdn.example/not-http.webp",
                "https://cdn.example/\x00bad.webp",
                "https:///missing-host.webp",
                BrokenUrl(),
            ],
            images_video=[
                "https://cdn.example/a.mp4",
                "http://[broken-ipv6",
            ],
        )

        result = self._adapter()._map_douyin(data, "https://douyin.com/video/1")

        assert result.media_type is MediaType.LIVE_PHOTO
        pairs = result.metadata["manifest"]["live_photos"]
        assert len(pairs) == 1
        assert pairs[0]["motion"]["url"] == "https://cdn.example/a.mp4"

    def test_uncoercible_aweme_type_falls_back_without_crashing(self):
        data = _fake_post_detail(
            aweme_type=float("inf"),
            images=[],
            images_video=[],
            video_play_addr=["https://cdn.example/video.mp4"],
        )

        assert self._adapter()._map_douyin(
            data, "https://douyin.com/video/1"
        ).media_type is MediaType.VIDEO

    def test_url_extraction_ignores_arbitrary_iterables_and_is_bounded(self):
        adapter = self._adapter()

        assert adapter._usable_urls((url for url in ["https://cdn.example/a.jpg"])) == []
        assert adapter._usable_urls({"items": ["https://cdn.example/a.jpg"]}) == []
        assert len(
            adapter._usable_urls(
                [f"https://cdn.example/{index}.jpg" for index in range(300)]
            )
        ) == 256


class TestWeiboMapping:
    def _adapter(self, **kwargs):
        return _offline_adapter(cookie_provider=FakeCookieProvider({"weibo": "SUB=x"}), **kwargs)

    def test_maps_video_weibo(self, monkeypatch):
        fake_handler = Mock(fetch_one_weibo=_async_returns(_fake_weibo_detail()))
        _stub_f2(monkeypatch, {
            ("f2.apps.weibo.utils", "WeiboIdFetcher"): Mock(get_weibo_id=_async_returns("wid")),
            ("f2.apps.weibo.handler", "WeiboHandler"): Mock(return_value=fake_handler),
        })

        result = self._adapter().parse(ParseCommand(urls=["https://weibo.com/1/AbC"]))[0]
        assert result.platform == "weibo"
        assert result.media_type is MediaType.VIDEO
        assert result.title == "示例微博"
        assert result.metadata["video_url"] == "https://cdn.example/w.mp4"
        assert result.metadata["source_id"] == "wid"
        assert result.metadata["published_at"] == "2026-09-05"

    def test_maps_image_weibo_with_cover_from_first_pic(self, monkeypatch):
        weibo_pic_infos = {
            "p1": {"url": "https://cdn.example/p1.jpg"},
            "p2": {"large": {"url": "https://cdn.example/p2.jpg"}},
        }
        fake_handler = Mock(
            fetch_one_weibo=_async_returns(
                _fake_weibo_detail(
                    playback_list=[], weibo_pic_infos=weibo_pic_infos, weibo_desc="图集微博"
                )
            )
        )
        _stub_f2(monkeypatch, {
            ("f2.apps.weibo.utils", "WeiboIdFetcher"): Mock(get_weibo_id=_async_returns("wid2")),
            ("f2.apps.weibo.handler", "WeiboHandler"): Mock(return_value=fake_handler),
        })

        result = self._adapter().parse(ParseCommand(urls=["https://weibo.com/1/AbC"]))[0]
        assert result.media_type is MediaType.IMAGE
        assert result.format == "jpg"
        assert result.cover == "https://cdn.example/p1.jpg"
        assert [img["url"] for img in result.metadata["images"]] == [
            "https://cdn.example/p1.jpg",
            "https://cdn.example/p2.jpg",
        ]

    def test_video_wins_when_images_also_present(self, monkeypatch):
        # A weibo post with BOTH a playback URL and pics is a video.
        fake_handler = Mock(
            fetch_one_weibo=_async_returns(
                _fake_weibo_detail(
                    weibo_pic_infos={"p1": {"url": "https://cdn.example/p1.jpg"}}
                )
            )
        )
        _stub_f2(monkeypatch, {
            ("f2.apps.weibo.utils", "WeiboIdFetcher"): Mock(get_weibo_id=_async_returns("wid3")),
            ("f2.apps.weibo.handler", "WeiboHandler"): Mock(return_value=fake_handler),
        })
        result = self._adapter().parse(ParseCommand(urls=["https://weibo.com/1/AbC"]))[0]
        assert result.media_type is MediaType.VIDEO
        assert result.metadata["video_url"] == "https://cdn.example/w.mp4"

    def test_none_first_playback_entry_is_skipped(self, monkeypatch):
        fake_handler = Mock(
            fetch_one_weibo=_async_returns(
                _fake_weibo_detail(playback_list=[None, "https://cdn.example/w2.mp4"])
            )
        )
        _stub_f2(monkeypatch, {
            ("f2.apps.weibo.utils", "WeiboIdFetcher"): Mock(get_weibo_id=_async_returns("wid4")),
            ("f2.apps.weibo.handler", "WeiboHandler"): Mock(return_value=fake_handler),
        })
        result = self._adapter().parse(ParseCommand(urls=["https://weibo.com/1/AbC"]))[0]
        assert result.media_type is MediaType.VIDEO
        assert result.metadata["video_url"] == "https://cdn.example/w2.mp4"


def _fake_tiktok_detail(**overrides):
    defaults = dict(
        api_status_code=0,
        nickname="tiktoker",
        uid="tu1",
        item_id="item-1",
        create_time="2026-09-04T08:00:00Z",
        desc="TikTok clip",
        video_playAddr="https://cdn.example/t.mp4",
        video_cover="https://cdn.example/tc.jpg",
        video_duration=65000,
    )
    defaults.update(overrides)
    return type("FakeTiktokDetail", (), defaults)()


class TestTiktokMapping:
    def _adapter(self, **kwargs):
        return _offline_adapter(cookie_provider=FakeCookieProvider({"tiktok": "t=1"}), **kwargs)

    def test_maps_video_detail(self, monkeypatch):
        fake_handler = Mock(fetch_one_video=_async_returns(_fake_tiktok_detail()))
        _stub_f2(monkeypatch, {
            ("f2.apps.tiktok.utils", "AwemeIdFetcher"): Mock(get_aweme_id=_async_returns("tid")),
            ("f2.apps.tiktok.handler", "TiktokHandler"): Mock(return_value=fake_handler),
        })

        result = self._adapter().parse(ParseCommand(urls=["https://www.tiktok.com/@u/video/1"]))[0]
        assert result.platform == "tiktok"
        assert result.media_type is MediaType.VIDEO
        assert result.title == "TikTok clip"
        assert result.cover == "https://cdn.example/tc.jpg"
        assert result.duration == "01:05"
        assert result.metadata["video_url"] == "https://cdn.example/t.mp4"
        assert result.metadata["source_id"] == "tid"
        assert result.metadata["published_at"] == "2026-09-04"

    def test_missing_video_addr_raises_parse_error(self, monkeypatch):
        fake_handler = Mock(
            fetch_one_video=_async_returns(_fake_tiktok_detail(video_playAddr=None))
        )
        _stub_f2(monkeypatch, {
            ("f2.apps.tiktok.utils", "AwemeIdFetcher"): Mock(get_aweme_id=_async_returns("tid2")),
            ("f2.apps.tiktok.handler", "TiktokHandler"): Mock(return_value=fake_handler),
        })

        with pytest.raises(EngineParseError):
            self._adapter().parse(ParseCommand(urls=["https://vm.tiktok.com/abc/"]))


class TestCookieHandling:
    def test_douyin_without_cookie_fails_fast_with_missing(self):
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({"douyin": None}))
        with pytest.raises(CookieMissingError):
            adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))

    def test_tiktok_without_cookie_fails_fast_with_missing(self):
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({"tiktok": None}))
        with pytest.raises(CookieMissingError):
            adapter.parse(ParseCommand(urls=["https://vm.tiktok.com/abc/"]))

    def test_weibo_without_cookie_still_parses(self, monkeypatch):
        # Public weibo posts parse without a cookie.
        fake_handler = Mock(fetch_one_weibo=_async_returns(_fake_weibo_detail()))
        _stub_f2(monkeypatch, {
            ("f2.apps.weibo.utils", "WeiboIdFetcher"): Mock(get_weibo_id=_async_returns("wid")),
            ("f2.apps.weibo.handler", "WeiboHandler"): Mock(return_value=fake_handler),
        })
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({"weibo": None}))
        result = adapter.parse(ParseCommand(urls=["https://weibo.com/1/AbC"]))[0]
        assert result.media_type is MediaType.VIDEO

    def test_no_cookie_provider_means_no_cookie(self):
        # With no provider wired, douyin behaves as "missing cookie".
        adapter = _offline_adapter()
        with pytest.raises(CookieMissingError):
            adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))

    def test_douyin_nonzero_status_with_cookie_is_cookie_invalid(self, monkeypatch):
        fake_handler = Mock(
            fetch_one_video=_async_returns(_fake_post_detail(api_status_code=4010))
        )
        _stub_f2(monkeypatch, {
            ("f2.apps.douyin.utils", "AwemeIdFetcher"): Mock(get_aweme_id=_async_returns("4")),
            ("f2.apps.douyin.handler", "DouyinHandler"): Mock(return_value=fake_handler),
        })
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({"douyin": "d=1"}))
        with pytest.raises(CookieInvalidError):
            adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))

    def test_tiktok_nonzero_status_is_cookie_invalid(self, monkeypatch):
        fake_handler = Mock(
            fetch_one_video=_async_returns(_fake_tiktok_detail(api_status_code=4010))
        )
        _stub_f2(monkeypatch, {
            ("f2.apps.tiktok.utils", "AwemeIdFetcher"): Mock(get_aweme_id=_async_returns("tid3")),
            ("f2.apps.tiktok.handler", "TiktokHandler"): Mock(return_value=fake_handler),
        })
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({"tiktok": "t=1"}))
        with pytest.raises(CookieInvalidError):
            adapter.parse(ParseCommand(urls=["https://vm.tiktok.com/abc/"]))

    def test_string_status_codes_are_coerced(self, monkeypatch):
        # f2's own handler compares str(status_code) == "0" — the API type is
        # not guaranteed int; "0" must NOT be treated as a cookie failure.
        fake_handler = Mock(
            fetch_one_video=_async_returns(_fake_post_detail(api_status_code="0"))
        )
        _stub_f2(monkeypatch, {
            ("f2.apps.douyin.utils", "AwemeIdFetcher"): Mock(get_aweme_id=_async_returns("7")),
            ("f2.apps.douyin.handler", "DouyinHandler"): Mock(return_value=fake_handler),
        })
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({"douyin": "d=1"}))
        result = adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))[0]
        assert result.media_type is MediaType.VIDEO

        fake_handler2 = Mock(
            fetch_one_video=_async_returns(_fake_post_detail(api_status_code="4010"))
        )
        _stub_f2(monkeypatch, {
            ("f2.apps.douyin.utils", "AwemeIdFetcher"): Mock(get_aweme_id=_async_returns("8")),
            ("f2.apps.douyin.handler", "DouyinHandler"): Mock(return_value=fake_handler2),
        })
        with pytest.raises(CookieInvalidError):
            adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))

    def test_weibo_20112_is_cookie_invalid(self, monkeypatch):
        fake_handler = Mock(
            fetch_one_weibo=_async_returns(_fake_weibo_detail(error_code=20112))
        )
        _stub_f2(monkeypatch, {
            ("f2.apps.weibo.utils", "WeiboIdFetcher"): Mock(get_weibo_id=_async_returns("wid")),
            ("f2.apps.weibo.handler", "WeiboHandler"): Mock(return_value=fake_handler),
        })
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({"weibo": "SUB=x"}))
        with pytest.raises(CookieInvalidError):
            adapter.parse(ParseCommand(urls=["https://weibo.com/1/AbC"]))

    def test_f2_unauthorized_error_is_cookie_invalid(self, monkeypatch):
        from f2.exceptions import APIUnauthorizedError

        async def boom(url):
            raise APIUnauthorizedError("rejected")

        _stub_f2(monkeypatch, {
            ("f2.apps.douyin.utils", "AwemeIdFetcher"): Mock(get_aweme_id=boom),
            # _fetch_douyin imports the handler before resolving the ID; stub it
            # so the APIUnauthorizedError from get_aweme_id is what surfaces.
            ("f2.apps.douyin.handler", "DouyinHandler"): Mock(return_value=Mock()),
        })
        adapter = _offline_adapter(cookie_provider=FakeCookieProvider({"douyin": "d=1"}))
        with pytest.raises(CookieInvalidError):
            adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))

    def test_f2_timeout_becomes_engine_timeout(self, monkeypatch):
        async def slow(url):
            await asyncio.sleep(5)

        _stub_f2(monkeypatch, {
            ("f2.apps.douyin.utils", "AwemeIdFetcher"): Mock(get_aweme_id=slow),
            # See test_f2_unauthorized_error_is_cookie_invalid: the handler
            # import precedes get_aweme_id, so it must be stubbed for the
            # wait_for deadline to fire on the ID resolution itself.
            ("f2.apps.douyin.handler", "DouyinHandler"): Mock(return_value=Mock()),
        })
        adapter = _offline_adapter(
            timeout_seconds=0.01, cookie_provider=FakeCookieProvider({"douyin": "d=1"})
        )
        with pytest.raises(EngineTimeoutError):
            adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))


class TestFileSizeProbe:
    def test_file_size_from_content_length(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-length": "1048576"}, content=b"")

        fake_handler = Mock(fetch_one_video=_async_returns(_fake_post_detail()))
        _stub_f2(monkeypatch, {
            ("f2.apps.douyin.utils", "AwemeIdFetcher"): Mock(get_aweme_id=_async_returns("5")),
            ("f2.apps.douyin.handler", "DouyinHandler"): Mock(return_value=fake_handler),
        })
        adapter = F2ParserAdapter(
            transport=httpx.MockTransport(handler),
            cookie_provider=FakeCookieProvider({"douyin": "d=1"}),
        )
        result = adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))[0]
        assert result.file_size_mb == pytest.approx(1.0)

    def test_probe_failure_leaves_size_none(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        fake_handler = Mock(fetch_one_video=_async_returns(_fake_post_detail()))
        _stub_f2(monkeypatch, {
            ("f2.apps.douyin.utils", "AwemeIdFetcher"): Mock(get_aweme_id=_async_returns("6")),
            ("f2.apps.douyin.handler", "DouyinHandler"): Mock(return_value=fake_handler),
        })
        adapter = F2ParserAdapter(
            transport=httpx.MockTransport(handler),
            cookie_provider=FakeCookieProvider({"douyin": "d=1"}),
        )
        result = adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))[0]
        assert result.file_size_mb is None
