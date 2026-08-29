"""SSRF-resistant HTTP access for upstream media resources.

The client resolves every host before connecting and gives the resulting address
to a transport that rewrites only the socket destination.  The original host is
kept in the request so HTTP virtual hosting and TLS SNI continue to work.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterator
from urllib.parse import urljoin, urlsplit

import httpx


class UnsafeUpstreamUrl(ValueError):
    """Raised when an upstream URL cannot be safely fetched."""


class UpstreamTooLarge(ValueError):
    """Raised when an upstream response exceeds its configured byte limit."""


Resolver = Callable[[str, int], list[str]]


@dataclass(frozen=True)
class SafeTarget:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


@dataclass
class UpstreamStream:
    status_code: int
    content_type: str | None
    headers: dict[str, str]
    chunks: Iterator[bytes]
    close: Callable[[], None]


def _system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def _is_forbidden_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise UnsafeUpstreamUrl("resolver returned an invalid address") from exc
    return any(
        (
            parsed.is_private,
            parsed.is_loopback,
            parsed.is_link_local,
            parsed.is_multicast,
            parsed.is_reserved,
            parsed.is_unspecified,
        )
    )


class PinnedIPTransport(httpx.BaseTransport):
    """Send a request to a validated address while preserving host identity.

    ``transport`` is intentionally injectable for tests and local adapters.  In
    production the default HTTP transport receives an IP URL, while the Host
    header and SNI extension retain the validated hostname.
    """

    def __init__(
        self,
        target: SafeTarget,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not target.addresses:
            raise UnsafeUpstreamUrl("target has no pinned address")
        self.target = target
        self._transport = transport or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        address = self.target.addresses[0]
        rewritten_url = request.url.copy_with(host=address)
        headers = request.headers.copy()
        host_header = self.target.host
        if self.target.port != (443 if request.url.scheme == "https" else 80):
            host_header = f"{host_header}:{self.target.port}"
        headers["host"] = host_header
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = self.target.host
        rewritten = httpx.Request(
            request.method,
            rewritten_url,
            headers=headers,
            content=request.stream,
            extensions=extensions,
        )
        return self._transport.handle_request(rewritten)

    def close(self) -> None:
        self._transport.close()


class SafeUpstreamClient:
    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        max_redirects: int = 3,
        max_bytes: int = 200 * 1024 * 1024,
    ) -> None:
        self._resolver = resolver or _system_resolver
        self._transport = transport
        self._timeout = timeout
        self._max_redirects = max_redirects
        self._max_bytes = max_bytes

    def validate(self, url: str) -> SafeTarget:
        try:
            parsed = urlsplit(url)
            scheme = parsed.scheme.lower()
            host = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise UnsafeUpstreamUrl("malformed upstream URL") from exc

        if scheme not in {"http", "https"}:
            raise UnsafeUpstreamUrl("upstream URL must use http or https")
        if not host:
            raise UnsafeUpstreamUrl("upstream URL has no hostname")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeUpstreamUrl("upstream URL cannot contain credentials")
        if host.lower().rstrip(".") in {"localhost", "localhost.localdomain"}:
            raise UnsafeUpstreamUrl("localhost is not an allowed upstream")
        if port is None:
            port = 443 if scheme == "https" else 80
        # A literal address must be checked independently of the resolver.  A
        # caller-provided resolver (including a test resolver) must not be able
        # to turn a private literal into an apparently public target.
        try:
            literal_address = ipaddress.ip_address(host)
        except ValueError:
            literal_address = None
        if literal_address is not None and any(
            (
                literal_address.is_private,
                literal_address.is_loopback,
                literal_address.is_link_local,
                literal_address.is_multicast,
                literal_address.is_reserved,
                literal_address.is_unspecified,
            )
        ):
            raise UnsafeUpstreamUrl("upstream URL contains a private address")
        try:
            addresses = self._resolve_and_check(host, port)
        except (OSError, socket.gaierror) as exc:
            raise UnsafeUpstreamUrl("upstream hostname could not be resolved") from exc
        return SafeTarget(url=url, host=host, port=port, addresses=tuple(addresses))

    def _resolve_and_check(self, host: str, port: int) -> list[str]:
        addresses = list(self._resolver(host, port))
        if not addresses:
            raise UnsafeUpstreamUrl("upstream hostname resolved to no addresses")
        for address in addresses:
            if _is_forbidden_address(address):
                raise UnsafeUpstreamUrl("upstream resolved to a private address")
        return addresses

    def _revalidate(self, target: SafeTarget) -> SafeTarget:
        try:
            addresses = self._resolve_and_check(target.host, target.port)
        except (OSError, socket.gaierror) as exc:
            raise UnsafeUpstreamUrl("upstream hostname could not be resolved") from exc
        return SafeTarget(target.url, target.host, target.port, tuple(addresses))

    def _client(self, target: SafeTarget) -> httpx.Client:
        transport = self._transport or PinnedIPTransport(target)
        return httpx.Client(transport=transport, timeout=self._timeout, follow_redirects=False)

    def _send(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        stream: bool,
    ) -> tuple[httpx.Response, httpx.Client]:
        current_url = url
        for redirect_count in range(self._max_redirects + 1):
            target = self._revalidate(self.validate(current_url))
            client = self._client(target)
            try:
                request = client.build_request("GET", current_url, headers=headers)
                response = client.send(request, stream=stream)
            except Exception:
                client.close()
                raise
            if response.is_redirect:
                location = response.headers.get("location")
                response.close()
                client.close()
                if not location or redirect_count >= self._max_redirects:
                    raise UnsafeUpstreamUrl("too many or invalid upstream redirects")
                current_url = urljoin(current_url, location)
                continue
            return response, client
        raise UnsafeUpstreamUrl("too many upstream redirects")

    def open(self, url: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        response, client = self._send(url, headers=headers, stream=False)
        try:
            response.read()
        except Exception:
            response.close()
            client.close()
            raise
        client.close()
        return response

    def stream(
        self,
        url: str,
        *,
        range_header: str | None = None,
        max_bytes: int | None = None,
    ) -> UpstreamStream:
        request_headers = {"Range": range_header} if range_header is not None else None
        response, client = self._send(url, headers=request_headers, stream=True)
        limit = self._max_bytes if max_bytes is None else max_bytes
        closed = False

        def close() -> None:
            nonlocal closed
            if not closed:
                closed = True
                response.close()
                client.close()

        try:
            declared = response.headers.get("content-length")
            declared_size = int(declared) if declared is not None else None
        except (TypeError, ValueError):
            declared_size = None
        if declared_size is not None and declared_size > limit:
            close()
            raise UpstreamTooLarge("upstream response exceeds byte limit")

        def chunks() -> Iterator[bytes]:
            total = 0
            try:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise UpstreamTooLarge("upstream response exceeds byte limit")
                    yield chunk
            except Exception:
                close()
                raise
            else:
                close()

        safe_headers = {
            name: value
            for name, value in response.headers.items()
            if name.lower()
            in {"accept-ranges", "content-length", "content-range", "etag", "last-modified"}
        }
        return UpstreamStream(
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
            headers=safe_headers,
            chunks=chunks(),
            close=close,
        )

    def read_limited(self, response: httpx.Response, *, max_bytes: int | None = None) -> bytes:
        limit = self._max_bytes if max_bytes is None else max_bytes
        declared = response.headers.get("content-length")
        try:
            declared_size = int(declared) if declared is not None else None
        except (TypeError, ValueError):
            declared_size = None
        if declared_size is not None and declared_size > limit:
            response.close()
            raise UpstreamTooLarge("upstream response exceeds byte limit")
        body = bytearray()
        try:
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > limit:
                    raise UpstreamTooLarge("upstream response exceeds byte limit")
            return bytes(body)
        finally:
            response.close()
