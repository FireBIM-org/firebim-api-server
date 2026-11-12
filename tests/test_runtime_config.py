"""Bounded check cache, Celery reliability settings and the shared S3 client factory."""

from unittest.mock import patch

import pytest

from app.checks.caching import CheckExecutionCache, LRUCache, make_graph_cache_key

TTL = "@prefix ex: <http://example.org/> .\nex:a ex:p ex:b ."


def test_lru_cache_evicts_least_recently_used():
    evicted: list[str] = []
    cache = LRUCache(2, on_evict=evicted.append)
    cache["a"] = 1
    cache["b"] = 2
    assert cache.get("a") == 1  # "a" is now the most recently used
    cache["c"] = 3
    assert list(cache) == ["a", "c"]
    assert evicted == ["b"]
    assert cache.get("b") is None


def test_check_cache_is_bounded_and_drops_derived_entries():
    cache = CheckExecutionCache(max_entries=2)
    keys = []
    for index in range(3):
        key, _graph = cache.get_or_parse_graph(graph_uri=f"http://example.org/g{index}", ttl=TTL)
        keys.append(key)
        cache.set_targets(key, "http://example.org/C", ["t"])
        cache.set_graph_schema(key, {"schema": index})
        cache.set_property_values(key, "t", "p", [index])

    assert len(cache.parsed_graphs) == 2
    assert keys[0] not in cache.parsed_graphs
    # Everything derived from the evicted graph went with it.
    assert cache.get_targets(keys[0], "http://example.org/C") is None
    assert cache.get_graph_schema(keys[0]) is None
    assert cache.get_property_values(keys[0], "t", "p") is None
    assert cache.get_targets(keys[2], "http://example.org/C") == ["t"]


def test_check_cache_hits_still_work():
    cache = CheckExecutionCache(max_entries=4)
    metrics: dict = {}
    first_key, first = cache.get_or_parse_graph(graph_uri="http://example.org/g", ttl=TTL, metrics=metrics)
    second_key, second = cache.get_or_parse_graph(graph_uri="http://example.org/g", ttl=TTL, metrics=metrics)
    assert first_key == second_key == make_graph_cache_key("http://example.org/g", TTL)
    assert first is second
    assert metrics["counters"] == {"parsed_graph_cache_misses": 1, "parsed_graph_cache_hits": 1}


def test_default_check_cache_uses_setting():
    from app.core.config import settings

    assert CheckExecutionCache().max_entries == settings.CHECK_CACHE_MAX_ENTRIES == 32


def test_celery_reliability_settings():
    from app.workers.celery_app import celery_app

    conf = celery_app.conf
    assert conf.task_time_limit == 3600
    assert conf.task_soft_time_limit == 3300
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.broker_connection_retry_on_startup is True


def test_celery_soft_limit_must_be_below_hard_limit():
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(CELERY_TASK_TIME_LIMIT=100, CELERY_TASK_SOFT_TIME_LIMIT=100)


def test_s3_factory_sets_timeouts_and_retries():
    from app.core.s3 import create_s3_client

    client = create_s3_client()
    config = client.meta.config
    assert config.connect_timeout == 5
    assert config.read_timeout == 60
    # botocore normalises max_attempts (retries) to total_max_attempts (first try + retries).
    assert config.retries == {"total_max_attempts": 6, "mode": "standard"}


@pytest.mark.parametrize(
    "factory",
    [
        lambda: __import__("app.storage.graph_store", fromlist=["GraphStoreClient"]).GraphStoreClient(),
        lambda: __import__("app.storage.s3_json_store", fromlist=["UserScopedS3JsonStore"]).UserScopedS3JsonStore(),
    ],
)
def test_stores_build_clients_through_the_shared_factory(factory):
    sentinel = object()
    with (
        patch("app.storage.graph_store.create_s3_client", return_value=sentinel),
        patch("app.storage.s3_json_store.create_s3_client", return_value=sentinel),
    ):
        assert factory().client is sentinel


def test_stores_keep_injected_clients():
    from app.storage.graph_store import GraphStoreClient
    from app.storage.s3_json_store import UserScopedS3JsonStore

    fake = object()
    assert GraphStoreClient(s3_client=fake).client is fake
    assert UserScopedS3JsonStore(s3_client=fake).client is fake
