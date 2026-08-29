from __future__ import annotations

import httpx
import pytest

from app.adapters.safe_upstream import (
    PinnedIPTransport,
    SafeUpstreamClient,
    UnsafeUpstreamUrl,
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


def test_read_limited_enforces_declared_and_streamed_sizes() -> None:
    client = SafeUpstreamClient(max_bytes=4)
    response = httpx.Response(200, headers={"content-length": "5"}, content=b"12345")
    with pytest.raises(UpstreamTooLarge):
        client.read_limited(response)

    response = httpx.Response(200, content=b"12345")
    with pytest.raises(UpstreamTooLarge):
        client.read_limited(response, max_bytes=4)


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
