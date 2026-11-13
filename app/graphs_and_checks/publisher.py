from __future__ import annotations

from app.rules.publisher import default_rule_graph_uri
from app.rules.publisher import fetch_graph_ttl as _fetch_graph_ttl
from app.rules.publisher import publish_graph_ttl as _publish_graph_ttl
from app.storage.building_repository import (
    default_building_graph_uri as _default_building_graph_uri,
)

BUILDING_GRAPH_DATASET = "buildings"
RULE_DATASET = "rulebook"


def default_building_graph_uri(graph_id: str) -> str:
    return _default_building_graph_uri(graph_id)


def publish_building_graph_ttl(ttl_text: str, graph_uri: str) -> None:
    _publish_graph_ttl(ttl_text=ttl_text, dataset=BUILDING_GRAPH_DATASET, graph_uri=graph_uri)


def fetch_building_graph_ttl(graph_uri: str) -> str:
    return _fetch_graph_ttl(dataset=BUILDING_GRAPH_DATASET, graph_uri=graph_uri)


def fetch_rule_ttl(rule_id: str) -> tuple[str, str]:
    graph_uri = default_rule_graph_uri(rule_id)
    ttl = _fetch_graph_ttl(dataset=RULE_DATASET, graph_uri=graph_uri)
    if not ttl or not str(ttl).strip():
        raise ValueError(f"Rule '{rule_id}' was not found or returned empty TTL")
    return graph_uri, str(ttl)
