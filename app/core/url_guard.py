"""Guard for server-side fetches of user-supplied URLs (SSRF protection).

A URL is fetched only when it is http(s), carries no credentials, matches the optional host allowlist and every
address its host resolves to is public: loopback, private, link-local (incl. the 169.254.169.254 metadata
endpoint), shared, reserved, multicast and unspecified ranges are refused, as are IPv6 forms that embed such an
IPv4 address. Redirects are followed by hand so every hop is checked the same way.

A fetch is also bounded in size and time: the body is streamed and abandoned once it passes ``max_bytes`` (a larger
``Content-Length`` is refused before reading), and ``timeout`` is a deadline for the whole fetch, redirects included,
not just for each network operation. Both raise :class:`FetchLimitError`, an ``httpx.HTTPError``.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable, Iterable
from urllib.parse import SplitResult, urljoin, urlsplit

import httpx

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Iterable[str]]

MAX_REDIRECTS = 5


class UnsafeUrlError(ValueError):
    """The URL may not be fetched by the server."""


class FetchLimitError(httpx.HTTPError):
    """The response was larger than allowed, or the fetch took longer than its deadline."""


def _system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"host {host!r} could not be resolved") from exc
    return [str(info[4][0]) for info in infos]


def is_public_address(address: IPAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        embedded = address.ipv4_mapped or address.sixtofour or (address.teredo[1] if address.teredo else None)
        if embedded is not None and not is_public_address(embedded):
            return False
        if address.scope_id:
            return False
    return address.is_global and not address.is_multicast


def _host_allowed(host: str, allowed_hosts: Iterable[str]) -> bool:
    allowed = [item.strip().lower().rstrip(".") for item in allowed_hosts if item.strip()]
    if not allowed:
        return True
    host = host.lower().rstrip(".")
    return any(host == item or (item.startswith(".") and host.endswith(item)) for item in allowed)


def check_url(url: str, *, allowed_hosts: Iterable[str] = ()) -> SplitResult:
    """Checks that need no network: scheme, credentials, allowlist and literal IP addresses."""
    try:
        parts = urlsplit(str(url).strip())
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrlError(f"invalid URL: {exc}") from exc
    if parts.scheme not in {"http", "https"}:
        raise UnsafeUrlError("only http(s) URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrlError("URLs with credentials are not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("URL has no host")
    if not _host_allowed(host, allowed_hosts):
        raise UnsafeUrlError(f"host {host!r} is not in the allowed hosts")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not is_public_address(literal):
        raise UnsafeUrlError(f"address {host} is not public")
    if port is not None and not 0 < port < 65536:
        raise UnsafeUrlError("invalid port")
    return parts


def resolve_and_check(url: str, *, allowed_hosts: Iterable[str] = (), resolver: Resolver | None = None) -> None:
    """``check_url`` plus DNS: every address the host resolves to must be public."""
    parts = check_url(url, allowed_hosts=allowed_hosts)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = list((resolver or _system_resolver)(parts.hostname or "", port))
    if not addresses:
        raise UnsafeUrlError(f"host {parts.hostname!r} did not resolve")
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError as exc:
            raise UnsafeUrlError(f"host {parts.hostname!r} resolved to an invalid address") from exc
        if not is_public_address(address):
            raise UnsafeUrlError(f"host {parts.hostname!r} resolves to a non-public address")


def _remaining(deadline: float, timeout: float, clock: Callable[[], float]) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise FetchLimitError(f"fetch took longer than {timeout:g}s")
    return remaining


def _read_capped(
    response: httpx.Response, *, max_bytes: int | None, deadline: float, timeout: float, clock: Callable[[], float]
) -> bytes:
    declared = response.headers.get("content-length", "")
    if max_bytes is not None and declared.isdigit() and int(declared) > max_bytes:
        raise FetchLimitError(f"response is larger than {max_bytes} bytes")
    chunks: list[bytes] = []
    received = 0
    for chunk in response.iter_bytes():
        received += len(chunk)
        if max_bytes is not None and received > max_bytes:
            raise FetchLimitError(f"response is larger than {max_bytes} bytes")
        _remaining(deadline, timeout, clock)
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_public_url(
    url: str,
    *,
    timeout: float,
    max_bytes: int | None = None,
    allowed_hosts: Iterable[str] = (),
    headers: dict[str, str] | None = None,
    resolver: Resolver | None = None,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> bytes:
    """GET ``url`` after checking it, re-checking every redirect hop. Raises UnsafeUrlError or httpx.HTTPError
    (FetchLimitError when the body passes ``max_bytes`` or the whole fetch takes longer than ``timeout`` seconds)."""
    allowed = list(allowed_hosts)
    deadline = clock() + timeout
    with httpx.Client(follow_redirects=False, headers=headers, transport=transport) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            resolve_and_check(current, allowed_hosts=allowed, resolver=resolver)
            # Each network operation may only use what is left of the overall deadline.
            request_timeout = httpx.Timeout(_remaining(deadline, timeout, clock))
            with client.stream("GET", current, timeout=request_timeout) as response:
                if response.is_redirect and "location" in response.headers:
                    current = urljoin(str(response.url), response.headers["location"])
                    continue
                response.raise_for_status()
                return _read_capped(response, max_bytes=max_bytes, deadline=deadline, timeout=timeout, clock=clock)
    raise UnsafeUrlError(f"too many redirects (more than {MAX_REDIRECTS})")


__all__ = [
    "FetchLimitError",
    "UnsafeUrlError",
    "check_url",
    "fetch_public_url",
    "is_public_address",
    "resolve_and_check",
]
