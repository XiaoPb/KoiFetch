"""Tests for the engine parser routing facade (Task 11).

The facade dispatches each URL to the f2 adapter (douyin/weibo/tiktok) or the
legacy parse-video-py adapter (everything else f2 does not cover), rejects
music URLs, and honours ``enable_legacy_fallback``. The inner adapters are
faked so the routing contract is tested in isolation — their own behavior is
covered by test_parser_f2.py / test_parser_legacy.py."""

import pytest

import app.adapters.parser_engine as parser_engine
from app.adapters.engine_errors import UnsupportedPlatformError
from app.adapters.parser_engine import EngineParserAdapter
from app.domain import MediaType, ParseCommand, ParseResult


class FakeResult:
    def __init__(self, url):
        self.url = url


class FakeF2Adapter:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []

    def parse(self, command):
        self.calls.extend(command.urls)
        return [FakeResult(url) for url in command.urls]


class FakeLegacyAdapter:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []

    def parse(self, command):
        self.calls.extend(command.urls)
        return [FakeResult(url) for url in command.urls]


@pytest.fixture(autouse=True)
def fake_adapters(monkeypatch):
    monkeypatch.setattr(parser_engine, "F2ParserAdapter", FakeF2Adapter)
    monkeypatch.setattr(parser_engine, "LegacyParserAdapter", FakeLegacyAdapter)


class TestRouting:
    def test_f2_urls_go_to_f2_adapter(self):
        adapter = EngineParserAdapter()
        results = adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))
        assert len(results) == 1
        assert adapter._f2().calls == ["https://v.douyin.com/abc/"]
        assert adapter._legacy().calls == []

    def test_legacy_urls_go_to_legacy_adapter(self):
        adapter = EngineParserAdapter()
        adapter.parse(ParseCommand(urls=["https://www.bilibili.com/video/BV1xx"]))
        assert adapter._legacy().calls == ["https://www.bilibili.com/video/BV1xx"]
        assert adapter._f2().calls == []

    def test_music_url_raises_unsupported(self):
        adapter = EngineParserAdapter()
        with pytest.raises(UnsupportedPlatformError):
            adapter.parse(ParseCommand(urls=["https://music.163.com/#/song?id=1"]))

    def test_unknown_platform_raises_unsupported(self):
        adapter = EngineParserAdapter()
        with pytest.raises(UnsupportedPlatformError):
            adapter.parse(ParseCommand(urls=["https://example.com/things/xyz"]))

    def test_quanminkge_stays_video_not_music(self):
        # kg.qq.com is 全民K歌 (video) in parse-video-py, not QQ music — the
        # legacy (video) route must win over the music route.
        adapter = EngineParserAdapter()
        adapter.parse(ParseCommand(urls=["https://kg.qq.com/node/play?s=abc"]))
        assert adapter._legacy().calls == ["https://kg.qq.com/node/play?s=abc"]
        assert adapter._f2().calls == []

    def test_hostname_false_positives_rejected(self):
        adapter = EngineParserAdapter()
        for url in (
            "https://box.com/video/1",
            "https://best.co/track/1",
            "https://36.cn/clip/1",
            "https://tv.sohu.com.evil.example/video/1",
            "https://weibo.com.evil/video/1",
            "https://douyin.com.evil.example/video/1",
        ):
            with pytest.raises(UnsupportedPlatformError):
                adapter.parse(ParseCommand(urls=[url]))


class TestLegacyFallbackSwitch:
    def test_legacy_disabled_makes_legacy_urls_unsupported(self):
        adapter = EngineParserAdapter(enable_legacy_fallback=False)
        with pytest.raises(UnsupportedPlatformError):
            adapter.parse(ParseCommand(urls=["https://www.bilibili.com/video/BV1xx"]))

    def test_legacy_disabled_keeps_f2_urls_working(self):
        adapter = EngineParserAdapter(enable_legacy_fallback=False)
        results = adapter.parse(ParseCommand(urls=["https://v.douyin.com/abc/"]))
        assert len(results) == 1

    def test_cookie_provider_is_forwarded_to_f2_only(self):
        provider = object()
        adapter = EngineParserAdapter(cookie_provider=provider)
        assert adapter._f2().kwargs["cookie_provider"] is provider
        assert "cookie_provider" not in adapter._legacy().kwargs


class TestFacadeConstruction:
    def test_adapters_are_built_lazily_and_shared(self):
        adapter = EngineParserAdapter()
        assert adapter._f2_adapter is None
        first = adapter._f2()
        assert adapter._f2() is first
        assert isinstance(adapter._f2_adapter, FakeF2Adapter)

    def test_constructor_passes_timeout_proxy_and_transport(self):
        transport = object()
        adapter = EngineParserAdapter(timeout_seconds=9.0, proxy="http://p:8080", transport=transport)
        assert adapter._f2().kwargs["timeout_seconds"] == 9.0
        assert adapter._f2().kwargs["proxy"] == "http://p:8080"
        assert adapter._f2().kwargs["transport"] is transport
