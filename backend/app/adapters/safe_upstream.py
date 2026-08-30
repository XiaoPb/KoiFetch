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

_DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (KoiFetch/0.1)"}


class UnsafeUpstreamUrl(ValueError):
    """Raised when an upstream URL cannot be safely fetched."""


class UpstreamTooLarge(ValueError):
    """Raised when an upstream response exceeds its configured byte limit."""


class UpstreamProtocolError(ValueError):
    """Raised when an upstream response contains invalid protocol metadata."""


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


def _close_resources(*resources: object, suppress: bool = False) -> None:
    """Close all resources, preserving the first close error when requested."""
    first_error: Exception | None = None
    for resource in resources:
        close = getattr(resource, "close", None)
        if close is None:
            continue
        try:
            close()
        except Exception as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None and not suppress:
        raise first_error


def _content_length(headers: httpx.Headers) -> int | None:
    raw = headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise UpstreamProtocolError("invalid upstream content-length") from exc
    if value < 0:
        raise UpstreamProtocolError("invalid upstream content-length")
    return value


def _read_body_limited(response: httpx.Response, limit: int) -> bytes:
    declared_size = _content_length(response.headers)
    if declared_size is not None and declared_size > limit:
        raise UpstreamTooLarge("upstream response exceeds byte limit")
    body = bytearray()
    for chunk in response.iter_bytes():
        if len(body) + len(chunk) > limit:
            raise UpstreamTooLarge("upstream response exceeds byte limit")
        body.extend(chunk)
    return bytes(body)


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
        *,
        proxy: str | None = None,
    ) -> None:
        if not target.addresses:
            raise UnsafeUpstreamUrl("target has no pinned address")
        self.target = target
        self._transport = transport or httpx.HTTPTransport(proxy=proxy)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        last_error: Exception | None = None
        for address in self.target.addresses:
            rewritten_url = request.url.copy_with(host=address)
            headers = request.headers.copy()
            host_name = self.target.host.strip("[]")
            host_header = f"[{host_name}]" if ":" in host_name else host_name
            if self.target.port != (443 if request.url.scheme == "https" else 80):
                host_header = f"{host_header}:{self.target.port}"
            headers["host"] = host_header
            extensions = dict(request.extensions)
            extensions["sni_hostname"] = host_name
            rewritten = httpx.Request(
                request.method,
                rewritten_url,
                headers=headers,
                content=request.stream,
                extensions=extensions,
            )
            try:
                return self._transport.handle_request(rewritten)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

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
        proxy: str | None = None,
    ) -> None:
        self._resolver = resolver or _system_resolver
        self._transport = transport
        self._timeout = timeout
        self._max_redirects = max_redirects
        self._max_bytes = max_bytes
        self._proxy = proxy

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
        transport = self._transport or PinnedIPTransport(target, proxy=self._proxy)
        return httpx.Client(transport=transport, timeout=self._timeout, follow_redirects=False)

    def _send(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
    ) -> tuple[httpx.Response, httpx.Client]:
        current_url = url
        for redirect_count in range(self._max_redirects + 1):
            target = self._revalidate(self.validate(current_url))
            client = self._client(target)
            try:
                request = client.build_request(method, current_url, headers=headers)
                # Always defer body consumption.  ``open`` applies its cap
                # itself, and redirects are discarded without being read.
                response = client.send(request, stream=True)
            except BaseException:
                _close_resources(client, suppress=True)
                raise
            if response.is_redirect:
                location = response.headers.get("location")
                _close_resources(response, client, suppress=True)
                if not location or redirect_count >= self._max_redirects:
                    raise UnsafeUpstreamUrl("too many or invalid upstream redirects")
                current_url = urljoin(current_url, location)
                continue
            return response, client
        raise UnsafeUpstreamUrl("too many upstream redirects")

    def open(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> httpx.Response:
        response, client = self._send(url, headers=headers)
        try:
            body = _read_body_limited(
                response, self._max_bytes if max_bytes is None else max_bytes
            )
        except BaseException:
            _close_resources(response, client, suppress=True)
            raise
        _close_resources(response, client)
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=body,
            request=response.request,
            extensions=response.extensions,
        )

    def head(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> httpx.Response:
        """Fetch response headers only through the same SSRF-safe pipeline."""
        response, client = self._send(url, method="HEAD", headers=headers)
        try:
            # A HEAD response has no body to consume, but an invalid declared
            # size must still be rejected before it reaches the API boundary.
            declared_size = _content_length(response.headers)
            if (
                declared_size is not None
                and max_bytes is not None
                and declared_size > max_bytes
            ):
                raise UpstreamTooLarge("upstream response exceeds byte limit")
            safe = httpx.Response(
                response.status_code,
                headers=response.headers,
                content=b"",
                request=response.request,
                extensions=response.extensions,
            )
        finally:
            _close_resources(response, client, suppress=True)
        return safe

    def stream(
        self,
        url: str,
        *,
        range_header: str | None = None,
        max_bytes: int | None = None,
    ) -> UpstreamStream:
        request_headers = dict(_DEFAULT_HEADERS)
        if range_header is not None:
            request_headers["Range"] = range_header
        response, client = self._send(url, headers=request_headers)
        limit = self._max_bytes if max_bytes is None else max_bytes
        closed = False

        def close() -> None:
            nonlocal closed
            if not closed:
                closed = True
                _close_resources(response, client)

        try:
            declared_size = _content_length(response.headers)
        except BaseException:
            _close_resources(response, client, suppress=True)
            raise
        if declared_size is not None and declared_size > limit:
            _close_resources(response, client, suppress=True)
            raise UpstreamTooLarge("upstream response exceeds byte limit")

        def chunks() -> Iterator[bytes]:
            total = 0
            try:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise UpstreamTooLarge("upstream response exceeds byte limit")
                    yield chunk
            except BaseException:
                try:
                    close()
                except Exception:
                    pass
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
        try:
            body = _read_body_limited(response, limit)
        except BaseException:
            _close_resources(response, suppress=True)
            raise
        _close_resources(response)
        return body
