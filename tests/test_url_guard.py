import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import ontologies as ontologies_api
from app.core.url_guard import FetchLimitError, UnsafeUrlError, check_url, fetch_public_url, resolve_and_check
from app.ontologies import registry_sync
from app.schemas.ontologies import OntologySyncRequest


def public_resolver(host: str, port: int) -> list[str]:
    return {"registry.example.org": ["93.184.215.14"], "evil.example.org": ["10.0.0.5"]}.get(host, ["93.184.215.15"])


@pytest.mark.parametrize(
    "url",
    [
        "ftp://registry.example.org/index.json",
        "file:///etc/passwd",
        "http://user:pass@registry.example.org/index.json",
        "http://user@registry.example.org/index.json",
        "http://127.0.0.1/index.json",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.43.0.1/",
        "http://192.168.1.10/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[fd00::1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://[::ffff:127.0.0.1]/",
        "http:///index.json",
    ],
)
def test_check_url_rejects_unsafe_urls(url):
    with pytest.raises(UnsafeUrlError):
        check_url(url)


def test_check_url_accepts_public_http_and_https():
    check_url("https://firebim.org/documentation/ontologies/index.json")
    check_url("http://93.184.215.14:8080/index.json")


def test_allowlist_limits_hosts():
    allowed = ["firebim.org", ".example.org"]
    check_url("https://firebim.org/index.json", allowed_hosts=allowed)
    check_url("https://registry.example.org/index.json", allowed_hosts=allowed)
    with pytest.raises(UnsafeUrlError):
        check_url("https://evilfirebim.org/index.json", allowed_hosts=allowed)


def test_dns_resolving_to_private_address_is_rejected():
    resolve_and_check("https://registry.example.org/index.json", resolver=public_resolver)
    with pytest.raises(UnsafeUrlError):
        resolve_and_check("https://evil.example.org/index.json", resolver=public_resolver)
    with pytest.raises(UnsafeUrlError):
        resolve_and_check("https://mixed.example.org/", resolver=lambda host, port: ["93.184.215.14", "127.0.0.1"])


def test_every_redirect_hop_is_checked():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "registry.example.org":
            return httpx.Response(302, headers={"Location": "http://evil.example.org/latest/meta-data"})
        return httpx.Response(200, content=b"secret")

    with pytest.raises(UnsafeUrlError):
        fetch_public_url(
            "https://registry.example.org/index.json",
            timeout=5,
            resolver=public_resolver,
            transport=httpx.MockTransport(handler),
        )


def test_safe_redirects_are_followed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old.json":
            return httpx.Response(301, headers={"Location": "/new.json"})
        return httpx.Response(200, content=b'{"ok": true}')

    body = fetch_public_url(
        "https://registry.example.org/old.json",
        timeout=5,
        resolver=public_resolver,
        transport=httpx.MockTransport(handler),
    )
    assert body == b'{"ok": true}'


def test_redirect_loops_stop():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/again"})

    with pytest.raises(UnsafeUrlError):
        fetch_public_url(
            "https://registry.example.org/",
            timeout=5,
            resolver=public_resolver,
            transport=httpx.MockTransport(handler),
        )


def test_declared_content_length_over_the_cap_is_refused_before_reading():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 200)

    with pytest.raises(FetchLimitError):
        fetch_public_url(
            "https://registry.example.org/big.ttl",
            timeout=5,
            max_bytes=100,
            resolver=public_resolver,
            transport=httpx.MockTransport(handler),
        )


def test_streamed_body_is_abandoned_once_it_passes_the_cap():
    sent: list[int] = []

    def chunks():
        for index in range(100):
            sent.append(index)
            yield b"x" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=chunks())  # chunked, no Content-Length

    with pytest.raises(FetchLimitError):
        fetch_public_url(
            "https://registry.example.org/endless.ttl",
            timeout=5,
            max_bytes=100,
            resolver=public_resolver,
            transport=httpx.MockTransport(handler),
        )
    assert len(sent) < 100

    small = fetch_public_url(
        "https://registry.example.org/small.ttl",
        timeout=5,
        max_bytes=100,
        resolver=public_resolver,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=iter([b"ab", b"cd"]))),
    )
    assert small == b"abcd"


def test_timeout_is_a_deadline_for_the_whole_fetch():
    ticks = iter(range(1000))

    def clock() -> float:
        return float(next(ticks))  # every look at the clock is one second later

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=iter([b"x"] * 50))  # a slow drip of small chunks

    with pytest.raises(FetchLimitError):
        fetch_public_url(
            "https://registry.example.org/drip.ttl",
            timeout=5,
            resolver=public_resolver,
            transport=httpx.MockTransport(handler),
            clock=clock,
        )


def test_guarded_fetcher_passes_the_size_cap(monkeypatch):
    from app.core.config import settings

    seen: dict = {}
    monkeypatch.setattr(registry_sync, "fetch_public_url", lambda url, **kwargs: seen.update(kwargs) or b"{}")
    registry_sync._guarded_fetcher("https://registry.example.org/index.json")
    assert seen["max_bytes"] == settings.ONTOLOGY_SYNC_MAX_FETCH_BYTES == 52_428_800
    assert seen["timeout"] == settings.ONTOLOGY_SYNC_TIMEOUT_SECONDS


def test_oversized_registry_index_is_a_registry_error():
    def too_big(url: str) -> bytes:
        raise FetchLimitError("response is larger than 100 bytes")

    with pytest.raises(registry_sync.OntologyRegistryError):
        registry_sync.fetch_registry("https://registry.example.org/index.json", fetcher=too_big)


def test_user_supplied_registry_uses_the_guarded_fetcher(monkeypatch):
    used: list[str] = []
    monkeypatch.setattr(registry_sync, "_default_fetcher", lambda url: used.append(("default", url)) or b"{}")

    def guarded(url: str) -> bytes:
        used.append(("guarded", url))
        raise UnsafeUrlError("non-public address")

    monkeypatch.setattr(registry_sync, "_guarded_fetcher", guarded)

    with pytest.raises(registry_sync.OntologySourceNotAllowedError):
        registry_sync.sync_from_registry(object(), OntologySyncRequest(source_url="https://elsewhere.example/i.json"))
    assert used == [("guarded", "https://elsewhere.example/i.json")]


def _sync_client(monkeypatch) -> TestClient:
    queued: list = []
    monkeypatch.setattr(ontologies_api, "celery_app", type("C", (), {"send_task": lambda *a, **k: queued.append(a)}))
    app = FastAPI()
    app.include_router(ontologies_api.router)
    app.dependency_overrides[ontologies_api.get_current_active_user] = lambda: type("U", (), {"id": "user-1"})()
    client = TestClient(app)
    client.queued = queued  # type: ignore[attr-defined]
    return client


@pytest.mark.parametrize("path", ["/ontologies/sync", "/ontologies/sync/preview", "/ontologies/sync/jobs"])
@pytest.mark.parametrize("source_url", ["http://169.254.169.254/latest/meta-data", "http://127.0.0.1:8000/x"])
def test_sync_endpoints_refuse_internal_source_urls(monkeypatch, path, source_url):
    client = _sync_client(monkeypatch)
    response = client.post(path, json={"source_url": source_url})
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"]
    assert client.queued == []  # type: ignore[attr-defined]


def test_sync_allowlist_setting_is_enforced(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ONTOLOGY_SYNC_ALLOWED_HOSTS", "firebim.org")
    client = _sync_client(monkeypatch)
    response = client.post("/ontologies/sync/preview", json={"source_url": "https://other.example.org/index.json"})
    assert response.status_code == 400
