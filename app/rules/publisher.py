import logging
from urllib.parse import urlencode

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Lightweight in-memory fallback index so list endpoints do not depend on SPARQL.
_GRAPH_INDEX = {
    "rules": {},
    "rule_elements": {},
}



def default_rule_graph_uri(rule_id: str) -> str:
    return f"https://firebim.org/graphs/rulebook/{rule_id}"



def default_ruleset_graph_uri(ruleset_id: str) -> str:
    # Backwards-compatible alias.
    return default_rule_graph_uri(ruleset_id)



def default_rule_element_graph_uri(element_id: str) -> str:
    return f"https://firebim.org/graphs/rule-elements/{element_id}"



def remember_rule_graph(graph_uri: str, rule_id: str, entry: str | None = None, label: str | None = None, element_count: int = 0) -> None:
    _GRAPH_INDEX["rules"][graph_uri] = {
        "graph_uri": graph_uri,
        "rule_id": rule_id,
        "ruleset_id": rule_id,
        "entry": entry,
        "label": label,
        "element_count": element_count,
        "node_count": element_count,
    }



def remember_rule_element_graph(graph_uri: str, element_id: str, element_type: str, label: str | None = None) -> None:
    _GRAPH_INDEX["rule_elements"][graph_uri] = {
        "graph_uri": graph_uri,
        "element_id": element_id,
        "rule_element_id": element_id,
        "element_type": element_type,
        "node_type": element_type,
        "label": label,
    }



def forget_graph(graph_uri: str) -> None:
    _GRAPH_INDEX["rules"].pop(graph_uri, None)
    _GRAPH_INDEX["rule_elements"].pop(graph_uri, None)



def list_rule_graphs() -> list[dict]:
    return sorted(_GRAPH_INDEX["rules"].values(), key=lambda item: item["graph_uri"])



def list_rule_element_graphs(dataset: str | None = None) -> list[str]:
    # dataset is accepted for backwards compatibility with existing call sites.
    return sorted(_GRAPH_INDEX["rule_elements"].keys())



def list_rule_element_entries() -> list[dict]:
    return sorted(_GRAPH_INDEX["rule_elements"].values(), key=lambda item: item["graph_uri"])



def _build_graph_store_endpoint(dataset: str, graph_uri: str) -> tuple[str, tuple[str, str] | None]:
    base = settings.FUSEKI_HOST.rstrip("/")
    endpoint = f"{base}/{dataset}/data?{urlencode({'graph': graph_uri})}"

    auth = None
    if settings.FUSEKI_USER and settings.FUSEKI_PASSWORD:
        auth = (settings.FUSEKI_USER, settings.FUSEKI_PASSWORD)

    return endpoint, auth



def publish_ttl_to_fuseki(ttl_text: str, dataset: str, graph_uri: str) -> None:
    endpoint, auth = _build_graph_store_endpoint(dataset, graph_uri)

    try:
        response = httpx.put(
            endpoint,
            content=ttl_text.encode("utf-8"),
            headers={"Content-Type": "text/turtle"},
            auth=auth,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise ValueError(f"Fuseki publish failed: {exc}") from exc

    if response.status_code not in (200, 201, 204):
        raise ValueError(f"Fuseki publish failed: {response.status_code} {response.text}")



def fetch_ttl_from_fuseki(dataset: str, graph_uri: str) -> str:
    endpoint, auth = _build_graph_store_endpoint(dataset, graph_uri)

    try:
        response = httpx.get(
            endpoint,
            headers={"Accept": "text/turtle"},
            auth=auth,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise ValueError(f"Fuseki fetch failed: {exc}") from exc

    if response.status_code != 200:
        raise ValueError(f"Fuseki fetch failed: {response.status_code} {response.text}")

    return response.text



def delete_graph_from_fuseki(dataset: str, graph_uri: str) -> None:
    endpoint, auth = _build_graph_store_endpoint(dataset, graph_uri)

    try:
        response = httpx.delete(
            endpoint,
            auth=auth,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise ValueError(f"Fuseki delete failed: {exc}") from exc

    if response.status_code not in (200, 202, 204):
        raise ValueError(f"Fuseki delete failed: {response.status_code} {response.text}")

    forget_graph(graph_uri)
