from __future__ import annotations

from rdflib import Graph

from app.storage.fuseki_client import FusekiClient, FusekiNotFoundError

BUILDINGS_DATASET = "buildings"
BUILDING_GRAPH_BASE_URI = "https://firebim.org/graphs/buildings/"


def default_building_graph_uri(graph_id: str) -> str:
    graph_id = str(graph_id).strip()
    if not graph_id:
        raise ValueError("graph_id must not be empty")
    return f"{BUILDING_GRAPH_BASE_URI}{graph_id}"


class BuildingRepository:
    def __init__(self, client: FusekiClient | None = None) -> None:
        self.client = client or FusekiClient()
        self.dataset = BUILDINGS_DATASET
        self.base_uri = BUILDING_GRAPH_BASE_URI

    def health(self) -> bool:
        return self.client.ping_dataset(self.dataset)

    def list_graphs(self) -> list[dict]:
        return self.client.list_named_graphs(
            dataset=self.dataset,
            base_uri=self.base_uri,
        )

    def exists(self, graph_uri: str) -> bool:
        try:
            ttl = self.client.get_graph_ttl(self.dataset, graph_uri)
            return bool(ttl.strip())
        except FusekiNotFoundError:
            return False

    def publish_graph(self, graph_id: str, ttl: str) -> tuple[str, bool]:
        graph_uri = default_building_graph_uri(graph_id)
        overwritten = self.exists(graph_uri)
        self._validate_ttl(ttl)
        self.client.put_graph_ttl(
            dataset=self.dataset,
            graph_uri=graph_uri,
            ttl=ttl,
        )
        return graph_uri, overwritten

    def delete_graph(self, graph_id: str) -> str:
        graph_uri = default_building_graph_uri(graph_id)
        self.client.delete_graph(
            dataset=self.dataset,
            graph_uri=graph_uri,
        )
        return graph_uri

    def fetch_graph(self, graph_id: str) -> tuple[str, str]:
        graph_uri = default_building_graph_uri(graph_id)
        ttl = self.client.get_graph_ttl(
            dataset=self.dataset,
            graph_uri=graph_uri,
        )
        return graph_uri, ttl

    @staticmethod
    def _validate_ttl(ttl: str) -> None:
        g = Graph()
        try:
            g.parse(data=ttl, format="turtle")
        except Exception as exc:
            raise ValueError(f"Invalid Turtle content: {exc}") from exc
