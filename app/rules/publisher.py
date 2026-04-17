from __future__ import annotations

from time import monotonic

from rdflib import RDF, Graph, Namespace
from rdflib.namespace import SH

from app.storage.graph_store import GraphStoreClient

RULEBOOK_DATASET = "rulebook"
RULE_GRAPH_BASE_URI = "https://firebim.org/graphs/rulebook/"
RULE_ELEMENT_GRAPH_BASE_URI = "https://firebim.org/graphs/rule-elements/"
EX = Namespace("http://example.org/firebim/")

_client = GraphStoreClient()

_RULE_ELEMENT_ENTRIES_CACHE: list[dict] | None = None
_RULE_ELEMENT_ENTRIES_CACHE_AT: float = 0.0
_RULE_ELEMENT_ENTRIES_CACHE_TTL_SECONDS = 5.0


def clear_rule_element_entries_cache() -> None:
    global _RULE_ELEMENT_ENTRIES_CACHE, _RULE_ELEMENT_ENTRIES_CACHE_AT
    _RULE_ELEMENT_ENTRIES_CACHE = None
    _RULE_ELEMENT_ENTRIES_CACHE_AT = 0.0


def default_rule_graph_uri(rule_id: str) -> str:
    rule_id = str(rule_id).strip()
    if not rule_id:
        raise ValueError("rule_id must not be empty")
    return f"{RULE_GRAPH_BASE_URI}{rule_id}"


def default_ruleset_graph_uri(ruleset_id: str) -> str:
    return default_rule_graph_uri(ruleset_id)


def default_rule_element_graph_uri(element_id: str) -> str:
    element_id = str(element_id).strip()
    if not element_id:
        raise ValueError("element_id must not be empty")
    return f"{RULE_ELEMENT_GRAPH_BASE_URI}{element_id}"


def publish_graph_ttl(ttl_text: str, dataset: str, graph_uri: str) -> None:
    _client.put_graph_ttl(dataset=dataset, graph_uri=graph_uri, ttl=ttl_text)


def fetch_graph_ttl(dataset: str, graph_uri: str) -> str:
    return _client.get_graph_ttl(dataset=dataset, graph_uri=graph_uri)


def delete_graph_ttl(dataset: str, graph_uri: str) -> None:
    _client.delete_graph(dataset=dataset, graph_uri=graph_uri)


def remember_rule_graph(*args, **kwargs) -> None:
    return None


def remember_rule_element_graph(
    graph_uri: str, *, element_id: str, element_type: str | None, label: str | None
) -> None:
    global _RULE_ELEMENT_ENTRIES_CACHE, _RULE_ELEMENT_ENTRIES_CACHE_AT

    item = {
        "element_id": element_id,
        "graph_uri": graph_uri,
        "element_type": element_type,
        "label": label,
    }

    if _RULE_ELEMENT_ENTRIES_CACHE is None:
        _RULE_ELEMENT_ENTRIES_CACHE = [item]
    else:
        retained = [entry for entry in _RULE_ELEMENT_ENTRIES_CACHE if entry["graph_uri"] != graph_uri]
        retained.append(item)
        _RULE_ELEMENT_ENTRIES_CACHE = sorted(retained, key=lambda entry: entry["graph_uri"])

    _RULE_ELEMENT_ENTRIES_CACHE_AT = monotonic()


def forget_graph(graph_uri: str) -> None:
    global _RULE_ELEMENT_ENTRIES_CACHE, _RULE_ELEMENT_ENTRIES_CACHE_AT

    if _RULE_ELEMENT_ENTRIES_CACHE is None:
        return

    _RULE_ELEMENT_ENTRIES_CACHE = [entry for entry in _RULE_ELEMENT_ENTRIES_CACHE if entry["graph_uri"] != graph_uri]
    _RULE_ELEMENT_ENTRIES_CACHE_AT = monotonic()


def _local_name(uri) -> str:
    text = str(uri)
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def _parse_rule_graph_metadata(ttl: str, graph_uri: str) -> dict:
    graph = Graph()
    graph.parse(data=ttl, format="turtle")

    ruleset_subjects = list(graph.subjects(RDF.type, EX.RuleSet))
    if ruleset_subjects:
        ruleset_uri = ruleset_subjects[0]
        entry_obj = graph.value(ruleset_uri, EX.entryPoint)
        label_obj = graph.value(graph.value(ruleset_uri, EX.entryPoint), EX.label) if entry_obj else None
        element_count = len(list(graph.subjects(RDF.type, SH.NodeShape))) + len(
            list(graph.subjects(RDF.type, EX.RuleElement))
        )
        return {
            "rule_id": _local_name(ruleset_uri),
            "graph_uri": graph_uri,
            "entry": _local_name(entry_obj) if entry_obj else None,
            "label": str(label_obj) if label_obj is not None else None,
            "element_count": element_count,
        }

    shapes = list(graph.subjects(RDF.type, SH.NodeShape))
    if shapes:
        return {
            "rule_id": graph_uri.rstrip("/").rsplit("/", 1)[-1],
            "graph_uri": graph_uri,
            "entry": None,
            "label": None,
            "element_count": len(shapes),
        }

    raise ValueError("No RuleSet or SHACL NodeShape resource found in TTL")


def _parse_rule_element_graph_metadata(ttl: str, graph_uri: str) -> dict:
    graph = Graph()
    graph.parse(data=ttl, format="turtle")

    shape = next(iter(graph.subjects(RDF.type, SH.NodeShape)), None)
    if shape is not None:
        label_obj = graph.value(shape, EX.label)
        return {
            "element_id": _local_name(shape),
            "graph_uri": graph_uri,
            "element_type": "decision",
            "label": str(label_obj) if label_obj is not None else None,
        }

    node = next(iter(graph.subjects(RDF.type, EX.RuleElement)), None)
    if node is None:
        raise ValueError("No RuleElement resource found in TTL")

    node_type_obj = graph.value(node, EX.nodeType)
    label_obj = graph.value(node, EX.label)
    return {
        "element_id": _local_name(node),
        "graph_uri": graph_uri,
        "element_type": str(node_type_obj) if node_type_obj is not None else None,
        "label": str(label_obj) if label_obj is not None else None,
    }


def list_rule_graphs() -> list[dict]:
    items = []
    for graph_entry in _client.list_named_graphs(dataset=RULEBOOK_DATASET, base_uri=RULE_GRAPH_BASE_URI):
        graph_uri = graph_entry["graph_uri"]
        ttl = fetch_graph_ttl(dataset=RULEBOOK_DATASET, graph_uri=graph_uri)
        metadata = _parse_rule_graph_metadata(ttl, graph_uri)
        metadata["graph_uri"] = graph_uri
        items.append(metadata)
    return sorted(items, key=lambda item: item["graph_uri"])


def list_rule_element_graphs(dataset: str | None = None) -> list[str]:
    graph_entries = _client.list_named_graphs(dataset=dataset or RULEBOOK_DATASET, base_uri=RULE_ELEMENT_GRAPH_BASE_URI)
    return sorted(item["graph_uri"] for item in graph_entries)


def list_rule_element_entries(force_refresh: bool = False) -> list[dict]:
    global _RULE_ELEMENT_ENTRIES_CACHE, _RULE_ELEMENT_ENTRIES_CACHE_AT

    now = monotonic()
    if (
        not force_refresh
        and _RULE_ELEMENT_ENTRIES_CACHE is not None
        and (now - _RULE_ELEMENT_ENTRIES_CACHE_AT) < _RULE_ELEMENT_ENTRIES_CACHE_TTL_SECONDS
    ):
        return list(_RULE_ELEMENT_ENTRIES_CACHE)

    cached_by_graph_uri = {entry["graph_uri"]: entry for entry in (_RULE_ELEMENT_ENTRIES_CACHE or [])}

    items = []
    for graph_uri in list_rule_element_graphs():
        cached = cached_by_graph_uri.get(graph_uri)
        if cached is not None:
            items.append(dict(cached))
            continue

        items.append(
            {
                "element_id": graph_uri.rstrip("/").rsplit("/", 1)[-1],
                "graph_uri": graph_uri,
                "element_type": None,
                "label": None,
            }
        )

    items = sorted(items, key=lambda item: item["graph_uri"])
    _RULE_ELEMENT_ENTRIES_CACHE = items
    _RULE_ELEMENT_ENTRIES_CACHE_AT = now
    return list(items)
