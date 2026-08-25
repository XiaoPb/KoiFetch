"""Tests for the typed engine exception hierarchy and its translation
helpers (Task 3). The stub era had no engine failures; real engines raise
httpx (parse-video-py) and requests (musicdl) exceptions that must map to
stable, bilingual client messages — never raw exception text."""

import httpx
import pytest

from app.adapters.engine_errors import (
    EngineDownloadError,
    EngineError,
    EngineNetworkError,
    EngineParseError,
    EngineTimeoutError,
    PlatformBlockedError,
    UnsupportedPlatformError,
    translate_engine_exception,
)

URL = "https://v.douyin.com/abc/"


def _request() -> httpx.Request:
    return httpx.Request("GET", URL)


class TestHierarchy:
    def test_all_errors_are_engine_errors(self):
        for error in (
            UnsupportedPlatformError("平台不支持 / Unsupported platform"),
            EngineNetworkError("网络错误 / Network error"),
            EngineTimeoutError("解析超时 / Parse timeout"),
            PlatformBlockedError("平台风控 / Blocked"),
            EngineParseError("解析失败 / Parse failed"),
            EngineDownloadError("下载失败 / Download failed"),
        ):
            assert isinstance(error, EngineError)

    def test_messages_are_stable_and_bilingual(self):
        error = EngineNetworkError("网络错误 / Network error")
        assert str(error) == "网络错误 / Network error"


class TestTranslation:
    def test_httpx_timeout_maps_to_engine_timeout(self):
        exc = httpx.ConnectTimeout("timed out", request=_request())
        translated = translate_engine_exception(exc, url=URL, operation="parse")
        assert isinstance(translated, EngineTimeoutError)
        assert str(translated) == "解析超时 / Parse timeout"

    def test_httpx_connect_error_maps_to_network(self):
        exc = httpx.ConnectError("connection refused", request=_request())
        translated = translate_engine_exception(exc, url=URL, operation="parse")
        assert isinstance(translated, EngineNetworkError)
        assert str(translated) == "网络错误 / Network error"

    def test_httpx_403_maps_to_platform_blocked(self):
        exc = httpx.HTTPStatusError(
            "Forbidden", request=_request(),
            response=httpx.Response(403, request=_request()),
        )
        translated = translate_engine_exception(exc, url=URL, operation="download")
        assert isinstance(translated, PlatformBlockedError)

    def test_httpx_other_status_maps_to_operation_error(self):
        exc = httpx.HTTPStatusError(
            "Server Error", request=_request(),
            response=httpx.Response(500, request=_request()),
        )
        assert isinstance(
            translate_engine_exception(exc, url=URL, operation="parse"),
            EngineParseError,
        )
        assert isinstance(
            translate_engine_exception(exc, url=URL, operation="download"),
            EngineDownloadError,
        )

    def test_unknown_exception_maps_to_operation_error(self):
        assert isinstance(
            translate_engine_exception(RuntimeError("boom"), url=URL, operation="parse"),
            EngineParseError,
        )

    def test_original_exception_is_preserved_as_cause(self):
        exc = httpx.ConnectError("nope", request=_request())
        with pytest.raises(EngineNetworkError) as raised:
            raise translate_engine_exception(exc, url=URL, operation="parse") from exc
        assert raised.value.__cause__ is exc
