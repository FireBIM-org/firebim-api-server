from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.rules.publisher import (
    delete_graph_from_fuseki as _delete_graph_from_fuseki,
    fetch_ttl_from_fuseki as _fetch_ttl_from_fuseki,
    publish_ttl_to_fuseki as _publish_ttl_to_fuseki,
)

BUILDING_GRAPH_DATASET = "buildings"
RULE_DATASET = "rulebook"

BUILDING_GRAPH_BASE_URI = "https://firebim.org/graphs/buildings/"
CHECK_RUN_BASE_URI = "https://firebim.org/check-runs/"

# Lightweight in-memory index for building graphs.
_GRAPH_INDEX: dict[str, dict] = {}


def default_building_graph_uri(graph_id: str) -> str:
    graph_id = str(graph_id).strip()
    if not graph_id:
        raise ValueError("graph_id must not be empty")
    return f"{BUILDING_GRAPH_BASE_URI}{graph_id}"


def default_check_run_uri(run_id: str) -> str:
    run_id = str(run_id).strip()
    if not run_id:
        raise ValueError("run_id must not be empty")
    return f"{CHECK_RUN_BASE_URI}{run_id}"


def publish_building_graph_ttl(ttl_text: str, graph_uri: str) -> None:
    _publish_ttl_to_fuseki(
        ttl_text=ttl_text,
        dataset=BUILDING_GRAPH_DATASET,
        graph_uri=graph_uri,
    )


def fetch_building_graph_ttl(graph_uri: str) -> str:
    return _fetch_ttl_from_fuseki(
        dataset=BUILDING_GRAPH_DATASET,
        graph_uri=graph_uri,
    )


def delete_building_graph(graph_uri: str) -> None:
    _delete_graph_from_fuseki(
        dataset=BUILDING_GRAPH_DATASET,
        graph_uri=graph_uri,
    )
    forget_building_graph(graph_uri)


def fetch_rule_ttl(rule_id: str) -> tuple[str, str]:
    from app.rules.publisher import default_rule_graph_uri

    graph_uri = default_rule_graph_uri(rule_id)
    ttl = _fetch_ttl_from_fuseki(
        dataset=RULE_DATASET,
        graph_uri=graph_uri,
    )
    if not ttl or not str(ttl).strip():
        raise ValueError(f"Rule '{rule_id}' was not found or returned empty TTL")
    return graph_uri, str(ttl)


def remember_building_graph(
    *,
    graph_uri: str,
    graph_id: str,
    label: str | None = None,
    source: str | None = None,
    triple_count: int = 0,
) -> None:
    _GRAPH_INDEX[graph_uri] = {
        "graph_uri": graph_uri,
        "graph_id": graph_id,
        "label": label,
        "source": source,
        "triple_count": triple_count,
    }


def forget_building_graph(graph_uri: str) -> None:
    _GRAPH_INDEX.pop(graph_uri, None)


def get_building_graph_entry(graph_uri: str) -> dict | None:
    return _GRAPH_INDEX.get(graph_uri)


def list_building_graphs() -> list[dict]:
    return sorted(_GRAPH_INDEX.values(), key=lambda item: item["graph_uri"])


def build_graph_store_endpoint(
    dataset: str, graph_uri: str
) -> tuple[str, tuple[str, str] | None]:
    base = settings.FUSEKI_HOST.rstrip("/")
    endpoint = f"{base}/{dataset}/data?{urlencode({'graph': graph_uri})}"

    auth = None
    if settings.FUSEKI_USER and settings.FUSEKI_PASSWORD:
        auth = (settings.FUSEKI_USER, settings.FUSEKI_PASSWORD)

    return endpoint, auth


def graph_exists(dataset: str, graph_uri: str) -> bool:
    endpoint, auth = build_graph_store_endpoint(dataset, graph_uri)
    try:
        response = httpx.get(
            endpoint,
            headers={"Accept": "text/turtle"},
            auth=auth,
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise ValueError(f"Fuseki existence check failed: {exc}") from exc

    return response.status_code == 200 and bool(response.text.strip())
