from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph, URIRef
from rdflib.namespace import RDF

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

ALLOWED_TARGET_PREFIXES = {
    "building_ifc_only_compliant.ttl": {
        "https://firebim.org/ontology/ifc/global/2026.04#",
    },
    "building_fbo_only_compliant.ttl": {
        "https://firebim.org/ontology/fbo/dk/2026.04#",
    },
    "building_ifc_fbo_compliant.ttl": {
        "https://firebim.org/ontology/ifc/global/2026.04#",
        "https://firebim.org/ontology/fbo/dk/2026.04#",
    },
    "building_ifc_rir_fbo_compliant.ttl": {
        "https://firebim.org/ontology/ifc/global/2026.04#",
        "https://firebim.org/ontology/fbo/dk/2026.04#",
        "https://firebim.org/ontology/rir/global/2026.04#",
    },
}


def _parse_fixture(name: str) -> Graph:
    graph = Graph()
    graph.parse(FIXTURE_DIR / name, format="turtle")
    return graph


def _is_allowed_target(uri: URIRef, allowed_prefixes: set[str]) -> bool:
    value = str(uri)
    if value in {str(RDF.type)}:
        return True
    return any(value.startswith(prefix) for prefix in allowed_prefixes)


@pytest.mark.parametrize("fixture_name", sorted(ALLOWED_TARGET_PREFIXES))
def test_demo_graph_fixture_parses(fixture_name: str):
    graph = _parse_fixture(fixture_name)
    assert len(graph) > 0


@pytest.mark.parametrize("fixture_name", sorted(ALLOWED_TARGET_PREFIXES))
def test_demo_graph_fixture_uses_only_expected_ontology_targets(fixture_name: str):
    graph = _parse_fixture(fixture_name)
    allowed_prefixes = ALLOWED_TARGET_PREFIXES[fixture_name]

    for _, predicate, obj in graph:
        assert _is_allowed_target(predicate, allowed_prefixes), predicate
        if predicate == RDF.type:
            assert isinstance(obj, URIRef)
            assert _is_allowed_target(obj, allowed_prefixes), obj
