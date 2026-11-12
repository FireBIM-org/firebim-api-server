"""Guard for server-side fetches of user-supplied URLs (SSRF protection).

A URL is fetched only when it is http(s), carries no credentials, matches the optional host allowlist and every
address its host resolves to is public: loopback, private, link-local (incl. the 169.254.169.254 metadata
endpoint), shared, reserved, multicast and unspecified ranges are refused, as are IPv6 forms that embed such an
IPv4 address. Redirects are followed by hand so every hop is checked the same way.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import SplitResult, urljoin, urlsplit

import httpx

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Iterable[str]]

MAX_REDIRECTS = 5


class UnsafeUrlError(ValueError):
    """The URL may not be fetched by the server."""


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


def fetch_public_url(
    url: str,
    *,
    timeout: float,
    allowed_hosts: Iterable[str] = (),
    headers: dict[str, str] | None = None,
    resolver: Resolver | None = None,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    """GET ``url`` after checking it, re-checking every redirect hop. Raises UnsafeUrlError or httpx.HTTPError."""
    allowed = list(allowed_hosts)
    with httpx.Client(
        timeout=httpx.Timeout(timeout), follow_redirects=False, headers=headers, transport=transport
    ) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            resolve_and_check(current, allowed_hosts=allowed, resolver=resolver)
            response = client.get(current)
            if response.is_redirect and "location" in response.headers:
                current = urljoin(str(response.url), response.headers["location"])
                continue
            response.raise_for_status()
            return response.content
    raise UnsafeUrlError(f"too many redirects (more than {MAX_REDIRECTS})")


__all__ = ["UnsafeUrlError", "check_url", "fetch_public_url", "is_public_address", "resolve_and_check"]
