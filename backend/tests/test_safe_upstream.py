from __future__ import annotations

import socket

import httpx
import pytest

from app.adapters.safe_upstream import (
    PinnedIPTransport,
    SafeTarget,
    SafeUpstreamClient,
    UnsafeUpstreamUrl,
    UpstreamProtocolError,
    UpstreamTooLarge,
)


def resolver_for(*addresses: str):
    def resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/media.mp4",
        "http://127.0.0.1/media.mp4",
        "http://[::1]/media.mp4",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.8/media.mp4",
        "http://172.16.2.4/media.mp4",
        "http://192.168.1.20/media.mp4",
    ],
)
def test_validate_rejects_local_and_metadata_targets(url: str) -> None:
    client = SafeUpstreamClient(resolver=resolver_for("93.184.216.34"))

    with pytest.raises(UnsafeUpstreamUrl):
        client.validate(url)


@pytest.mark.parametrize("address", ["10.0.0.1", "172.16.0.1", "192.168.0.1", "::1", "fe80::1"])
def test_validate_rejects_private_addresses(address: str) -> None:
    client = SafeUpstreamClient(resolver=resolver_for(address))

    with pytest.raises(UnsafeUpstreamUrl):
        client.validate("https://cdn.example/media.mp4")


def test_validate_rejects_embedded_credentials_and_missing_host() -> None:
    client = SafeUpstreamClient(resolver=resolver_for("93.184.216.34"))

    with pytest.raises(UnsafeUpstreamUrl):
        client.validate("https://user:password@cdn.example/media.mp4")
    with pytest.raises(UnsafeUpstreamUrl):
        client.validate("https:///media.mp4")


def test_open_revalidates_redirect_target() -> None:
    calls: list[tuple[str, int]] = []

    def resolve(host: str, port: int) -> list[str]:
        calls.append((host, port))
        return ["93.184.216.34"] if host == "cdn.example" else ["10.0.0.2"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://private.example/file"}, request=request)

    client = SafeUpstreamClient(resolver=resolve, transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeUpstreamUrl):
        client.open("https://cdn.example/file")
    assert calls == [("cdn.example", 443), ("cdn.example", 443), ("private.example", 443)]


def test_open_rejects_dns_rebinding_before_connect() -> None:
    resolutions = iter([["93.184.216.34"], ["192.168.1.50"]])
    connected = False

    def resolve(host: str, port: int) -> list[str]:
        return next(resolutions)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal connected
        connected = True
        return httpx.Response(200, content=b"ok", request=request)

    client = SafeUpstreamClient(resolver=resolve, transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeUpstreamUrl):
        client.open("https://cdn.example/file")
    assert connected is False


def test_open_rejects_resolution_failure_during_revalidation() -> None:
    calls = 0
    connected = False

    def resolve(host: str, port: int) -> list[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ["93.184.216.34"]
        raise socket.gaierror("temporary resolver failure")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal connected
        connected = True
        return httpx.Response(200, content=b"ok", request=request)

    client = SafeUpstreamClient(resolver=resolve, transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeUpstreamUrl):
        client.open("https://cdn.example/file")
    assert connected is False


class RecordingStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.yielded: list[bytes] = []

    def __iter__(self):
        for chunk in self.chunks:
            self.yielded.append(chunk)
            yield chunk


def test_open_caps_body_accumulation_before_consuming_more_chunks() -> None:
    body = RecordingStream([b"123", b"45", b"6"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=body, request=request)

    client = SafeUpstreamClient(
        resolver=resolver_for("93.184.216.34"), transport=httpx.MockTransport(handler), max_bytes=4
    )
    with pytest.raises(UpstreamTooLarge):
        client.open("https://cdn.example/file")
    assert body.yielded == [b"123", b"45"]


def test_open_does_not_buffer_redirect_body() -> None:
    redirect_body = RecordingStream([b"discarded"])
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                302,
                headers={"location": "https://cdn.example/final"},
                stream=redirect_body,
                request=request,
            )
        return httpx.Response(200, content=b"ok", request=request)

    client = SafeUpstreamClient(
        resolver=resolver_for("93.184.216.34"), transport=httpx.MockTransport(handler)
    )
    assert client.open("https://cdn.example/file").content == b"ok"
    assert redirect_body.yielded == []


def test_read_limited_enforces_declared_and_streamed_sizes() -> None:
    client = SafeUpstreamClient(max_bytes=4)
    response = httpx.Response(200, headers={"content-length": "5"}, content=b"12345")
    with pytest.raises(UpstreamTooLarge):
        client.read_limited(response)

    response = httpx.Response(200, content=b"12345")
    with pytest.raises(UpstreamTooLarge):
        client.read_limited(response, max_bytes=4)


@pytest.mark.parametrize("content_length", ["not-a-number", "-1"])
def test_read_limited_rejects_malformed_content_length(content_length: str) -> None:
    client = SafeUpstreamClient(max_bytes=4)
    response = httpx.Response(200, headers={"content-length": content_length}, content=b"")
    with pytest.raises(UpstreamProtocolError, match="content-length"):
        client.read_limited(response)


def test_stream_rejects_declared_size_before_reading_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "5"}, content=b"")

    client = SafeUpstreamClient(
        resolver=resolver_for("93.184.216.34"), transport=httpx.MockTransport(handler), max_bytes=4
    )
    with pytest.raises(UpstreamTooLarge):
        client.stream("https://cdn.example/file")


def test_stream_sends_range_forwards_safe_headers_and_closes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            206,
            headers={
                "content-type": "video/mp4",
                "content-length": "3",
                "content-range": "bytes 0-2/3",
                "accept-ranges": "bytes",
                "etag": "abc",
                "last-modified": "today",
                "x-secret": "must-not-forward",
            },
            content=b"abc",
            request=request,
        )

    client = SafeUpstreamClient(
        resolver=resolver_for("93.184.216.34"), transport=httpx.MockTransport(handler)
    )
    stream = client.stream("https://cdn.example/file", range_header="bytes=0-2")
    assert seen[0].headers["range"] == "bytes=0-2"
    assert stream.status_code == 206
    assert stream.content_type == "video/mp4"
    assert stream.headers == {
        "accept-ranges": "bytes",
        "content-length": "3",
        "content-range": "bytes 0-2/3",
        "etag": "abc",
        "last-modified": "today",
    }
    assert b"".join(stream.chunks) == b"abc"


def test_stream_rejects_oversized_body_without_content_length() -> None:
    body = RecordingStream([b"123", b"45"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=body, request=request)

    client = SafeUpstreamClient(
        resolver=resolver_for("93.184.216.34"), transport=httpx.MockTransport(handler), max_bytes=4
    )
    stream = client.stream("https://cdn.example/file")
    with pytest.raises(UpstreamTooLarge):
        list(stream.chunks)
    assert body.yielded == [b"123", b"45"]


def test_pinned_transport_rewrites_address_and_retains_host_and_sni() -> None:
    target = SafeUpstreamClient(resolver=resolver_for("93.184.216.34")).validate(
        "https://cdn.example:8443/media.mp4"
    )
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["host"] = request.headers["host"]
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, content=b"ok", request=request)

    transport = PinnedIPTransport(target, transport=httpx.MockTransport(handler))
    with httpx.Client(transport=transport) as client:
        client.get(target.url)
    assert seen == {
        "url": "https://93.184.216.34:8443/media.mp4",
        "host": "cdn.example:8443",
        "sni": "cdn.example",
    }


def test_pinned_transport_retries_each_validated_address() -> None:
    target = SafeTarget(
        url="https://cdn.example/media.mp4",
        host="cdn.example",
        port=443,
        addresses=("93.184.216.34", "93.184.216.35"),
    )
    seen: list[str] = []

    class FailoverTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if len(seen) == 1:
                raise httpx.ConnectError("first address unavailable", request=request)
            return httpx.Response(200, content=b"ok", request=request)

        def close(self) -> None:
            pass

    transport = PinnedIPTransport(target, transport=FailoverTransport())
    with httpx.Client(transport=transport) as client:
        assert client.get(target.url).content == b"ok"
    assert seen == [
        "https://93.184.216.34/media.mp4",
        "https://93.184.216.35/media.mp4",
    ]


def test_pinned_transport_brackets_ipv6_host_authority() -> None:
    target = SafeTarget(
        url="https://[2001:db8::1]:8443/media.mp4",
        host="2001:db8::1",
        port=8443,
        addresses=("2001:db8::2",),
    )
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["host"] = request.headers["host"]
        seen["sni"] = request.extensions["sni_hostname"]
        return httpx.Response(200, content=b"ok", request=request)

    with httpx.Client(transport=PinnedIPTransport(target, httpx.MockTransport(handler))) as client:
        client.get(target.url)
    assert seen == {
        "url": "https://[2001:db8::2]:8443/media.mp4",
        "host": "[2001:db8::1]:8443",
        "sni": "2001:db8::1",
    }


def test_explicit_stream_close_closes_client_when_response_close_raises() -> None:
    class CloseTrackingTransport(httpx.MockTransport):
        closed = False

        def close(self) -> None:
            self.closed = True

    def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(200, content=b"ok", request=request)

        def bad_close() -> None:
            raise RuntimeError("response close failed")

        response.close = bad_close  # type: ignore[method-assign]
        return response

    transport = CloseTrackingTransport(handler)
    client = SafeUpstreamClient(resolver=resolver_for("93.184.216.34"), transport=transport)
    stream = client.stream("https://cdn.example/file")
    with pytest.raises(RuntimeError, match="response close failed"):
        stream.close()
    assert transport.closed is True
