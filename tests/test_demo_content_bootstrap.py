from __future__ import annotations

import os
from pathlib import Path

import pytest
from rdflib import Graph

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

from app.storage.building_repository import default_building_graph_uri
from app.storage.demo_content_bootstrap import GRAPH_FIXTURES, bootstrap_default_demo_content


class FakeBuildingRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def publish_graph(self, graph_id: str, ttl: str) -> tuple[str, bool]:
        Graph().parse(data=ttl, format="turtle")
        self.calls.append((graph_id, ttl))
        return default_building_graph_uri(graph_id), False


def test_bootstrap_default_demo_content_seeds_expected_graph_ids():
    repo = FakeBuildingRepository()

    seeded = bootstrap_default_demo_content(repo=repo)

    seeded_ids = [item["graph_id"] for item in seeded]
    assert seeded_ids == list(GRAPH_FIXTURES.keys())
    assert [graph_id for graph_id, _ in repo.calls] == list(GRAPH_FIXTURES.keys())
    assert "ifc-only-graph-compliant" in seeded_ids
    assert "fbo-only-graph-compliant" in seeded_ids
    assert "ifc-fbo-graph-compliant" in seeded_ids
    assert "ifc-rir-fbo-graph-compliant" in seeded_ids


@pytest.mark.parametrize("fixture_name", GRAPH_FIXTURES.values())
def test_bootstrap_fixtures_exist(fixture_name: str):
    fixture_path = Path(__file__).resolve().parent / "fixtures" / fixture_name
    assert fixture_path.exists(), fixture_name
