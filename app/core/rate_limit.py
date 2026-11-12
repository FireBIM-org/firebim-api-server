"""In-memory request rate limiting as a pure ASGI middleware.

Identity:
- requests carrying a bearer token are keyed on ``sha256(token)``. The token is *not* verified here, and the
  unverified ``sub`` claim is never used, so a forged token can only ever throttle itself.
- requests without a token are keyed on the client IP. ``X-Forwarded-For`` is honoured only when the direct
  peer is a trusted proxy; the right-most address that is not itself a trusted proxy wins.

The webapp proxies every browser request from its pod IPs, so IP limits apply to tokenless requests only.

Algorithm: GCRA (generic cell rate algorithm). Each (bucket, key) stores one float, the theoretical arrival
time; a limit of N per period allows bursts of N and then one request per ``period / N``. Buckets whose
theoretical arrival time is in the past are fully refilled and are swept away, and the total number of keys is
capped (least recently used first), so memory stays bounded. State is per process: with several replicas each
one enforces the limit on its own share of the traffic.
"""

from __future__ import annotations

import hashlib
import ipaddress
import math
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import metrics

_PERIOD_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
_RATE_LIMIT_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d*)\s*(second|minute|hour|day)s?\s*$", re.IGNORECASE)

# Floating point slack when comparing arrival times, so exactly N requests fit into a burst of N.
_EPSILON = 1e-9

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class RateLimit:
    limit: int
    period_seconds: float

    @property
    def emission_interval(self) -> float:
        return self.period_seconds / self.limit


def parse_rate_limit(value: str) -> RateLimit:
    """Parse ``"300/minute"``, ``"20/second"``, ``"1000/hour"``, ``"5000/day"`` or ``"10/5minutes"``."""
    match = _RATE_LIMIT_RE.match(str(value or ""))
    if not match:
        raise ValueError(f"Invalid rate limit {value!r}; expected e.g. '300/minute', '20/second' or '1000/hour'")
    count = int(match.group(1))
    multiplier = int(match.group(2) or 1)
    if count < 1 or multiplier < 1:
        raise ValueError(f"Invalid rate limit {value!r}; count and period must be positive")
    return RateLimit(limit=count, period_seconds=float(multiplier * _PERIOD_SECONDS[match.group(3).lower()]))


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: float


class GcraRateLimiter:
    """Bounded in-memory GCRA store. Not thread-safe; the middleware only calls it from the event loop."""

    def __init__(
        self,
        *,
        max_keys: int = 100_000,
        sweep_interval_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tats: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._max_keys = max_keys
        self._sweep_interval = sweep_interval_seconds
        self._clock = clock
        self._next_sweep = clock() + sweep_interval_seconds

    def __len__(self) -> int:
        return len(self._tats)

    def hit(self, checks: Iterable[tuple[str, str, RateLimit]]) -> list[RateLimitDecision]:
        """Count one request against every ``(bucket, key, limit)``.

        The request is admitted only when every limit admits it; a rejected request consumes nothing, so
        hitting the expensive-route limit does not also drain the general budget.
        """
        now = self._clock()
        self._maybe_sweep(now)
        decisions: list[RateLimitDecision] = []
        updates: list[tuple[tuple[str, str], float]] = []
        for bucket, key, rate in checks:
            store_key = (bucket, key)
            tat = max(self._tats.get(store_key, now), now)
            new_tat = tat + rate.emission_interval
            allow_at = new_tat - rate.period_seconds
            if allow_at - now > _EPSILON:
                decisions.append(RateLimitDecision(False, rate.limit, 0, allow_at - now))
                continue
            remaining = int((rate.period_seconds - (new_tat - now)) / rate.emission_interval + _EPSILON)
            decisions.append(RateLimitDecision(True, rate.limit, max(remaining, 0), 0.0))
            updates.append((store_key, new_tat))

        if all(decision.allowed for decision in decisions):
            for store_key, new_tat in updates:
                self._tats[store_key] = new_tat
                self._tats.move_to_end(store_key)
            while len(self._tats) > self._max_keys:
                self._tats.popitem(last=False)
        return decisions

    def _maybe_sweep(self, now: float) -> None:
        if now < self._next_sweep:
            return
        self._next_sweep = now + self._sweep_interval
        idle = [store_key for store_key, tat in self._tats.items() if tat <= now]
        for store_key in idle:
            del self._tats[store_key]


# Routes that are costly in CPU, memory, storage or outbound traffic. They count against RATE_LIMIT_EXPENSIVE
# in addition to RATE_LIMIT_DEFAULT. Classified by method + full path (regex, optional trailing slash).
EXPENSIVE_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", r"/ifc-models"),
    ("POST", r"/ifc-models/[^/]+/data-payload"),
    ("POST", r"/ids-files"),
    ("POST", r"/checks/run(-[a-z-]+)?"),
    ("POST", r"/graphs/publish"),
    ("POST", r"/rules/publish"),
    ("POST", r"/ontologies/publish"),
    ("POST", r"/ontologies/sync(/preview|/jobs)?"),
    ("POST", r"/ontologies/conversions/[^/]+"),
    ("POST", r"/ontologies/candidates/dictionaries/[^/]+/(convert|publish)"),
    ("POST", r"/rulebuilder/workspaces/[^/]+/export-rule-elements"),
)
_EXPENSIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (method, re.compile(f"{pattern}/?")) for method, pattern in EXPENSIVE_ROUTES
)

# Probes, build info and API docs are never limited.
EXEMPT_PATHS: frozenset[str] = frozenset(
    {"/livez", "/readyz", "/health", "/build-info", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
)


def is_expensive_route(method: str, path: str) -> bool:
    method = method.upper()
    return any(method == route_method and pattern.fullmatch(path) for route_method, pattern in _EXPENSIVE_PATTERNS)


def _parse_ip(value: str) -> IPAddress | None:
    value = value.strip().strip('"')
    if not value:
        return None
    if value.startswith("["):  # "[2001:db8::1]:443"
        value = value[1:].split("]", 1)[0]
    elif value.count(":") == 1:  # "203.0.113.7:51234"
        value = value.split(":", 1)[0]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def parse_trusted_networks(cidrs: Iterable[str]) -> tuple[IPNetwork, ...]:
    return tuple(ipaddress.ip_network(cidr.strip(), strict=False) for cidr in cidrs if cidr.strip())


def resolve_client_ip(
    peer: str | None, forwarded_for: str | None, trusted_networks: tuple[IPNetwork, ...]
) -> str | None:
    """The client address: the direct peer, or, behind a trusted proxy, the right-most untrusted X-Forwarded-For hop."""

    def trusted(address: IPAddress) -> bool:
        return any(address.version == network.version and address in network for network in trusted_networks)

    peer_ip = _parse_ip(peer or "")
    if peer_ip is None:
        return peer or None
    if not forwarded_for or not trusted(peer_ip):
        return str(peer_ip)

    candidate = peer_ip
    for hop in reversed(forwarded_for.split(",")):
        hop_ip = _parse_ip(hop)
        if hop_ip is None:
            # Garbage in the chain: stop at the last address a trusted proxy vouched for.
            break
        candidate = hop_ip
        if not trusted(hop_ip):
            break
    return str(candidate)


def _header(scope: Scope, name: bytes) -> str | None:
    values = [value.decode("latin-1") for key, value in scope.get("headers", []) if key.lower() == name]
    return ",".join(values) if values else None


def _bearer_token(scope: Scope) -> str | None:
    authorization = _header(scope, b"authorization")
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        default: str | RateLimit,
        expensive: str | RateLimit,
        anon: str | RateLimit,
        trusted_proxy_cidrs: Iterable[str] = (),
        enabled: bool = True,
        limiter: GcraRateLimiter | None = None,
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.default = default if isinstance(default, RateLimit) else parse_rate_limit(default)
        self.expensive = expensive if isinstance(expensive, RateLimit) else parse_rate_limit(expensive)
        self.anon = anon if isinstance(anon, RateLimit) else parse_rate_limit(anon)
        self.trusted_networks = parse_trusted_networks(trusted_proxy_cidrs)
        self.limiter = limiter if limiter is not None else GcraRateLimiter()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or not self.enabled
            or scope.get("method") == "OPTIONS"
            or scope.get("path", "") in EXEMPT_PATHS
        ):
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "")
        token = _bearer_token(scope)
        if token:
            identity = "token:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
            checks = [("default", identity, self.default)]
            if is_expensive_route(method, path):
                checks.append(("expensive", identity, self.expensive))
        else:
            client = scope.get("client")
            peer = client[0] if client else None
            client_ip = resolve_client_ip(peer, _header(scope, b"x-forwarded-for"), self.trusted_networks)
            checks = [("anon", f"ip:{client_ip or 'unknown'}", self.anon)]

        decisions = self.limiter.hit(checks)
        rejected = [
            (bucket, decision)
            for (bucket, _, _), decision in zip(checks, decisions, strict=True)
            if not decision.allowed
        ]
        if rejected:
            bucket, decision = max(rejected, key=lambda item: item[1].retry_after_seconds)
            metrics.record_rate_limited(bucket)
            retry_after = max(1, math.ceil(decision.retry_after_seconds))
            response = JSONResponse(
                {"detail": "Rate limit exceeded, retry later", "code": "RATE_LIMITED"},
                status_code=429,
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
            await response(scope, receive, send)
            return

        # Report the tightest limit on successful responses too, so clients can pace themselves.
        tightest = min(decisions, key=lambda decision: decision.remaining)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-RateLimit-Limit"] = str(tightest.limit)
                headers["X-RateLimit-Remaining"] = str(tightest.remaining)
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = [
    "EXEMPT_PATHS",
    "EXPENSIVE_ROUTES",
    "GcraRateLimiter",
    "RateLimit",
    "RateLimitDecision",
    "RateLimitMiddleware",
    "is_expensive_route",
    "parse_rate_limit",
    "resolve_client_ip",
]
