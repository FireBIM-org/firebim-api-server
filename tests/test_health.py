"""/health: the dependency checks run concurrently and each one is bounded, so a hung dependency cannot stall it."""

import asyncio
import threading
import time

import pytest

from app.api import health


@pytest.fixture
def bounded_checks(monkeypatch):
    """Run the checks as in a deployment (in threads, with timeouts) instead of the inline test shortcut."""
    monkeypatch.setattr(health.settings, "ENVIRONMENT", "local")
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.5)
    # Hung dependencies block on this until the test is done with them.
    release = threading.Event()
    yield release
    release.set()


def test_celery_check_stops_at_the_first_worker_reply(client, mock_celery):
    mock_celery.control.ping.return_value = [{"celery@worker-1": {"ok": "pong"}}]

    response = client.get("/health")

    assert response.json()["checks"]["celery"] == "ok"
    mock_celery.control.ping.assert_called_once_with(timeout=health.CELERY_PING_TIMEOUT_SECONDS, limit=1)
    assert health.CELERY_PING_TIMEOUT_SECONDS < health.CHECK_TIMEOUT_SECONDS <= 3


def test_celery_check_reports_no_workers(client, mock_celery):
    mock_celery.control.ping.return_value = []

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["checks"]["celery"] == "no workers responding"


def test_health_is_bounded_when_every_dependency_hangs(bounded_checks, monkeypatch, mock_s3, mock_celery):
    release = bounded_checks

    def hang(*_args, **_kwargs):
        release.wait(5)

    async def hang_async():
        await asyncio.sleep(5)

    monkeypatch.setattr(health, "_check_db_sync", hang)
    mock_s3.head_bucket.side_effect = hang
    mock_celery.control.ping.side_effect = hang
    monkeypatch.setattr(health, "_fetch_authentik_discovery", hang_async)

    async def timed_checks():
        started = time.monotonic()
        try:
            return await health.dependency_checks(), time.monotonic() - started
        finally:
            release.set()  # let the abandoned threads finish so the event loop can shut down

    results, elapsed = asyncio.run(timed_checks())

    assert results == [
        ("database", "error", False),
        ("s3", "error", False),
        ("celery", "error", False),
        ("authentik", "error", False),
    ]
    # Concurrent and individually bounded: about one timeout in total, not four.
    assert elapsed < 2 * health.CHECK_TIMEOUT_SECONDS


def test_health_reports_healthy_dependencies_with_timeouts_enabled(bounded_checks, monkeypatch, mock_celery):
    async def discovery_ok():
        return None

    monkeypatch.setattr(health, "_check_db_sync", lambda: None)
    monkeypatch.setattr(health, "_fetch_authentik_discovery", discovery_ok)
    mock_celery.control.ping.return_value = [{"celery@worker-1": {"ok": "pong"}}]

    results = asyncio.run(health.dependency_checks())

    assert all(ok for _name, _status, ok in results)
