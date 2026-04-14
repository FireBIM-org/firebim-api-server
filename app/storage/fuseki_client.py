from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.core.config import settings


class FusekiError(Exception):
    pass


class FusekiNotFoundError(FusekiError):
    pass


class FusekiClient:
    def __init__(
        self,
        host: str | None = None,
        user: str | None = None,
        password: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.host = (host or settings.FUSEKI_HOST).rstrip("/")
        self.user = user if user is not None else settings.FUSEKI_APP_USER
        self.password = (
            password
            if password is not None
            else (settings.FUSEKI_APP_PASSWORD.get_secret_value() if settings.FUSEKI_APP_PASSWORD is not None else None)
        )
        self.timeout = timeout

    @property
    def auth(self) -> tuple[str, str] | None:
        if self.user and self.password:
            return (self.user, self.password)
        return None

    def graph_store_url(self, dataset: str, graph_uri: str) -> str:
        return f"{self.host}/{dataset}/data?{urlencode({'graph': graph_uri})}"

    def sparql_url(self, dataset: str) -> str:
        return f"{self.host}/{dataset}/sparql"

    def ping_dataset(self, dataset: str) -> bool:
        query = "ASK { ?s ?p ?o }"
        try:
            response = httpx.post(
                self.sparql_url(dataset),
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
                auth=self.auth,
                timeout=self.timeout,
            )
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def put_graph_ttl(self, dataset: str, graph_uri: str, ttl: str) -> None:
        try:
            response = httpx.put(
                self.graph_store_url(dataset, graph_uri),
                content=ttl.encode("utf-8"),
                headers={"Content-Type": "text/turtle"},
                auth=self.auth,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise FusekiError(f"Fuseki publish failed: {exc}") from exc

        if response.status_code not in (200, 201, 204):
            raise FusekiError(f"Fuseki publish failed: {response.status_code} {response.text}")

    def get_graph_ttl(self, dataset: str, graph_uri: str) -> str:
        try:
            response = httpx.get(
                self.graph_store_url(dataset, graph_uri),
                headers={"Accept": "text/turtle"},
                auth=self.auth,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise FusekiError(f"Fuseki fetch failed: {exc}") from exc

        if response.status_code == 404:
            raise FusekiNotFoundError(f"Named graph not found: {graph_uri}")

        if response.status_code != 200:
            raise FusekiError(f"Fuseki fetch failed: {response.status_code} {response.text}")

        return response.text

    def delete_graph(self, dataset: str, graph_uri: str) -> None:
        try:
            response = httpx.delete(
                self.graph_store_url(dataset, graph_uri),
                auth=self.auth,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise FusekiError(f"Fuseki delete failed: {exc}") from exc

        if response.status_code == 404:
            raise FusekiNotFoundError(f"Named graph not found: {graph_uri}")

        if response.status_code not in (200, 202, 204):
            raise FusekiError(f"Fuseki delete failed: {response.status_code} {response.text}")

    def list_named_graphs(self, dataset: str, base_uri: str) -> list[dict]:
        query = f"""
        SELECT ?graph (COUNT(?s) AS ?tripleCount)
        WHERE {{
          GRAPH ?graph {{
            ?s ?p ?o .
          }}
          FILTER(STRSTARTS(STR(?graph), "{base_uri}"))
        }}
        GROUP BY ?graph
        ORDER BY ?graph
        """

        try:
            response = httpx.post(
                self.sparql_url(dataset),
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
                auth=self.auth,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise FusekiError(f"Fuseki list failed: {exc}") from exc

        if response.status_code != 200:
            raise FusekiError(f"Fuseki list failed: {response.status_code} {response.text}")

        payload = response.json()
        bindings = payload.get("results", {}).get("bindings", [])

        items: list[dict] = []
        for row in bindings:
            graph_uri = row["graph"]["value"]
            triple_count_raw = row.get("tripleCount", {}).get("value")
            triple_count = int(triple_count_raw) if triple_count_raw is not None else None
            items.append(
                {
                    "graph_uri": graph_uri,
                    "triple_count": triple_count,
                }
            )
        return items
