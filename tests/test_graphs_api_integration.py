from __future__ import annotations

import os
from pathlib import Path

import pytest
import requests
from rdflib import Graph, Namespace
from rdflib.namespace import RDF

API_BASE_URL = os.getenv("FIREBIM_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT = float(os.getenv("FIREBIM_API_TIMEOUT", "20"))

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FBO = Namespace("http://example.org/fbo/1.0/")

GRAPH_FIXTURES = {
    "ifc-inspired-small": FIXTURE_DIR / "building_small_ifc_inspired.ttl",
    "ifc-inspired-medium": FIXTURE_DIR / "building_medium_ifc_inspired.ttl",
    "ifc-inspired-large": FIXTURE_DIR / "building_large_ifc_inspired.ttl",
}

DELETE_TEST_GRAPH_ID = "ifc-inspired-delete-temp"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FIREBIM_RUN_INTEGRATION") != "1",
        reason="requires a running FireBIM API stack; set FIREBIM_RUN_INTEGRATION=1 to enable",
    ),
]


def _request(method: str, path: str, **kwargs) -> requests.Response:
    return requests.request(method, f"{API_BASE_URL}{path}", timeout=TIMEOUT, **kwargs)


def _load_ttl(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_ttl(ttl: str) -> Graph:
    graph = Graph()
    graph.parse(data=ttl, format="turtle")
    return graph


def _count_type(graph: Graph, class_name: str) -> int:
    return sum(1 for _ in graph.triples((None, RDF.type, FBO[class_name])))


def _count_predicate(graph: Graph, predicate_name: str) -> int:
    return sum(1 for _ in graph.triples((None, FBO[predicate_name], None)))


def _has_type(graph: Graph, class_name: str) -> bool:
    return _count_type(graph, class_name) > 0


def _list_graphs() -> dict:
    response = _request("GET", "/graphs")
    response.raise_for_status()
    return response.json()


def _graph_exists(graph_id: str) -> bool:
    graph_uri = f"https://firebim.org/graphs/buildings/{graph_id}"
    body = _list_graphs()
    return graph_uri in body.get("graph_uris", [])


def _ensure_graph_present(graph_id: str, ttl: str) -> tuple[dict, bool]:
    if _graph_exists(graph_id):
        fetch_response = _request("GET", f"/graphs/{graph_id}")
        fetch_response.raise_for_status()
        return fetch_response.json(), False

    publish_response = _request(
        "POST",
        "/graphs/publish",
        json={"graph_id": graph_id, "ttl": ttl},
    )
    publish_response.raise_for_status()

    fetch_response = _request("GET", f"/graphs/{graph_id}")
    fetch_response.raise_for_status()
    return fetch_response.json(), True


@pytest.fixture(scope="session")
def api_health():
    response = _request("GET", "/graphs/health")
    response.raise_for_status()
    return response.json()


@pytest.fixture(scope="session")
def ensured_graphs(api_health):
    result = {}
    for graph_id, ttl_path in GRAPH_FIXTURES.items():
        ttl = _load_ttl(ttl_path)
        fetched, created = _ensure_graph_present(graph_id, ttl)
        parsed = _parse_ttl(fetched["ttl"])
        result[graph_id] = {
            "created": created,
            "ttl_expected": ttl,
            "fetch": fetched,
            "graph": parsed,
        }
    return result


def test_graphs_health(api_health):
    assert api_health["status"] in {"ok", "degraded"}
    assert api_health["dataset"] == "buildings"
    assert api_health["storage_connected"] is True
    assert api_health["storage_backend"] == "s3"


@pytest.mark.parametrize("graph_id", list(GRAPH_FIXTURES.keys()))
def test_publish_or_reuse_graphs(ensured_graphs, graph_id):
    body = ensured_graphs[graph_id]["fetch"]
    graph = ensured_graphs[graph_id]["graph"]

    assert body["success"] is True
    assert body["graph_id"] == graph_id
    assert body["graph_uri"] == f"https://firebim.org/graphs/buildings/{graph_id}"
    assert len(graph) > 0


def test_list_graphs_contains_fixtures(ensured_graphs):
    body = _list_graphs()
    expected_uris = {f"https://firebim.org/graphs/buildings/{graph_id}" for graph_id in GRAPH_FIXTURES}
    returned_uris = set(body.get("graph_uris", []))
    assert expected_uris.issubset(returned_uris)
    assert body["count"] >= len(expected_uris)


@pytest.mark.parametrize("graph_id", list(GRAPH_FIXTURES.keys()))
def test_fetch_graph_returns_parseable_ttl(ensured_graphs, graph_id):
    response = _request("GET", f"/graphs/{graph_id}")
    response.raise_for_status()
    body = response.json()
    graph = _parse_ttl(body["ttl"])

    assert body["success"] is True
    assert body["graph_id"] == graph_id
    assert len(graph) > 0
    assert _has_type(graph, "Site")
    assert _has_type(graph, "Building")


def test_small_graph_shape(ensured_graphs):
    graph = ensured_graphs["ifc-inspired-small"]["graph"]

    assert _has_type(graph, "Site")
    assert _has_type(graph, "Building")
    assert _has_type(graph, "Storey")
    assert _has_type(graph, "Zone")
    assert _has_type(graph, "Room")
    assert _has_type(graph, "Wall")
    assert _has_type(graph, "Window")
    assert _has_type(graph, "SensorTypeSmokeSensor")
    assert _count_predicate(graph, "hasBuilding") >= 1
    assert _count_predicate(graph, "hasStorey") >= 1
    assert _count_predicate(graph, "hasZone") >= 1
    assert _count_predicate(graph, "hasRoom") >= 1


def test_medium_graph_has_multiple_storeys_rooms_and_stairs(ensured_graphs):
    graph = ensured_graphs["ifc-inspired-medium"]["graph"]

    assert _count_type(graph, "Storey") >= 3
    assert _count_type(graph, "Zone") >= 4
    assert _count_type(graph, "Room") >= 5
    assert _count_type(graph, "Wall") >= 5
    assert _count_type(graph, "Window") >= 4
    assert _count_type(graph, "Stair") >= 2
    assert _count_type(graph, "Floor") >= 2
    assert _count_type(graph, "Ceiling") >= 2
    assert _count_predicate(graph, "adjacentToZone") >= 1
    assert _count_predicate(graph, "hasRoom") >= 5


def test_large_graph_has_four_buildings_and_cross_storey_patterns(ensured_graphs):
    graph = ensured_graphs["ifc-inspired-large"]["graph"]

    assert _count_type(graph, "Building") >= 4
    assert _count_type(graph, "Storey") >= 12
    assert _count_type(graph, "Zone") >= 12
    assert _count_type(graph, "Room") >= 24
    assert _count_type(graph, "Stair") >= 4
    assert _count_predicate(graph, "spansStorey") >= 4
    assert _count_predicate(graph, "locatedInStorey") >= 8
    assert _count_predicate(graph, "adjacentToZone") >= 4
    assert _count_predicate(graph, "hasRoom") >= 24


def test_delete_api_only_for_temp_graph():
    ttl = _load_ttl(GRAPH_FIXTURES["ifc-inspired-small"])
    temp_graph_id = DELETE_TEST_GRAPH_ID

    _ensure_graph_present(temp_graph_id, ttl)

    delete_response = _request("DELETE", f"/graphs/{temp_graph_id}")
    delete_response.raise_for_status()
    delete_body = delete_response.json()

    assert delete_body["success"] is True
    assert delete_body["graph_id"] == temp_graph_id
    assert delete_body["graph_uri"] == f"https://firebim.org/graphs/buildings/{temp_graph_id}"

    fetch_response = _request("GET", f"/graphs/{temp_graph_id}")
    assert fetch_response.status_code == 404


def test_delete_does_not_remove_core_fixtures(ensured_graphs):
    for graph_id in GRAPH_FIXTURES:
        response = _request("GET", f"/graphs/{graph_id}")
        response.raise_for_status()
        body = response.json()
        assert body["success"] is True
        assert body["graph_id"] == graph_id


if __name__ == "__main__":
    print("FireBIM integration test module")
    print(f"API base URL: {API_BASE_URL}")
    print("Fixtures:")
    for graph_id, path in GRAPH_FIXTURES.items():
        print(f"  - {graph_id}: {path}")
