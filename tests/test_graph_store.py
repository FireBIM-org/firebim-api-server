from __future__ import annotations

import os
from io import BytesIO

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("POSTGRES_DB", "firebim")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("S3_BUCKET_NAME", "firebim")
os.environ.setdefault("S3_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("S3_SECRET_ACCESS_KEY", "minioadmin")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

from app.storage.graph_store import (
    GraphStoreClient,
    GraphStoreNotFoundError,
)


class FakeS3Client:
    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.bucket_name = "firebim"

    def head_bucket(self, Bucket):
        if Bucket != self.bucket_name:
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000, ContinuationToken=None):
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        return {
            "IsTruncated": False,
            "Contents": [{"Key": key, **self.objects[key]["listing"]} for key in keys[:MaxKeys]],
        }

    def put_object(self, Bucket, Key, Body, ContentType=None, Metadata=None):
        self.objects[Key] = {
            "body": Body,
            "metadata": Metadata or {},
            "listing": {},
        }

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": BytesIO(self.objects[Key]["body"])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"Metadata": self.objects[Key]["metadata"]}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


@pytest.fixture()
def store():
    return GraphStoreClient(
        bucket_name="firebim",
        s3_client=FakeS3Client(),
    )


def test_graph_store_round_trip_and_list(store: GraphStoreClient):
    graph_uri = "https://firebim.org/graphs/buildings/demo"
    ttl = '@prefix ex: <http://example.org/> .\nex:a ex:b "c" .'

    assert store.ping_dataset("buildings") is True

    store.put_graph_ttl("buildings", graph_uri, ttl)

    assert store.get_graph_ttl("buildings", graph_uri) == ttl
    assert store.list_named_graphs("buildings", "https://firebim.org/graphs/buildings/") == [
        {
            "graph_uri": graph_uri,
            "triple_count": 1,
            "label": None,
            "description": None,
        }
    ]


def test_graph_store_lists_the_graphs_self_description(store: GraphStoreClient):
    """A graph that labels its own graph URI is listed with that label and comment (non-ASCII survives)."""
    graph_uri = "https://firebim.org/graphs/buildings/dk-office-1.0.0"
    ttl = (
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        f'<{graph_uri}> rdfs:label "Office · 1.0.0" ;\n'
        '  rdfs:comment "Expected: dk-compartments-size-ak1 → Pass; dk-unit-composition → Fail (≥ 1 exit)" .\n'
    )
    store.put_graph_ttl("buildings", graph_uri, ttl)
    metadata = store.client.objects[next(iter(store.client.objects))]["metadata"]
    assert metadata["label"].isascii() and metadata["description"].isascii()

    [item] = store.list_named_graphs("buildings", "https://firebim.org/graphs/buildings/")
    assert item["label"] == "Office · 1.0.0"
    assert item["description"].startswith("Expected: dk-compartments-size-ak1 → Pass")


def test_graph_store_delete_missing_raises(store: GraphStoreClient):
    with pytest.raises(GraphStoreNotFoundError):
        store.delete_graph("rulebook", "https://firebim.org/graphs/rulebook/missing")


def test_graph_store_uses_precomputed_triple_count(store: GraphStoreClient, monkeypatch):
    graph_uri = "https://firebim.org/graphs/ontologies/large"
    ttl = '@prefix ex: <http://example.org/> .\nex:a ex:b "c" .'

    def fail_if_parsed(_ttl: str) -> int:
        raise AssertionError("TTL should not be parsed twice")

    monkeypatch.setattr(store, "_triple_count", fail_if_parsed)
    store.put_graph_ttl("ontologies", graph_uri, ttl, triple_count=123)

    key = store._object_key("ontologies", graph_uri)
    assert store.client.objects[key]["metadata"]["triple-count"] == "123"
